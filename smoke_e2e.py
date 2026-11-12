"""End-to-end check of an installed Windows build, driven over HTTP.

Run by .github/workflows/windows-e2e.yml after the real installer has been
executed on a clean Windows runner. It exercises what a user's first session
does — models downloaded and loaded, a scanned PDF anonymised — and measures
the peak memory of the app's whole process tree, because the office machines
have 8 GB and a build that does not fit there swaps for hours.

Every document here is synthetic. The repository is public and so are the CI
logs: never point this at a real client document.

Usage:
    python smoke_e2e.py --port 7860 --budget-gb 4.5 --out report.json
Exit status is non-zero on any functional failure, or when ``--budget-gb`` is
above zero and the measured peak exceeds it.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import threading
import time
from pathlib import Path

import psutil
import requests

# Synthetic people and identifiers. 12345678Z and 87654321X carry valid DNI
# control letters, so the deterministic recognizer must find them whatever the
# statistical models do.
PAGES = [
    [
        "JUZGADO DE PRIMERA INSTANCIA N.º 3 DE ZARAGOZA",
        "",
        "Comparece D. JUAN PÉREZ MARTÍNEZ, mayor de edad,",
        "con DNI 12345678Z y domicilio en Calle Mayor 15,",
        "50001 Zaragoza, en su propio nombre y derecho.",
    ],
    [
        "Y de otra parte Dña. LUCÍA FERNÁNDEZ RUIZ,",
        "con DNI 87654321X, vecina de Huesca,",
        "teléfono 612 345 678.",
    ],
    [
        "Ambas partes se reconocen capacidad suficiente",
        "para otorgar el presente documento y lo firman.",
        "",
        "Fdo.: JUAN PÉREZ MARTÍNEZ",
    ],
]


def make_scanned_pdf() -> bytes:
    """A PDF whose pages are images only, so the OCR path has to run."""
    import fitz
    from PIL import Image, ImageDraw, ImageFont

    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font = (ImageFont.truetype(str(font_path), 40) if font_path.is_file()
            else ImageFont.load_default())
    doc = fitz.open()
    for lines in PAGES:
        img = Image.new("L", (1654, 2339), 255)  # A4 at 200 dpi
        draw = ImageDraw.Draw(img)
        y = 200
        for line in lines:
            draw.text((150, y), line, fill=0, font=font)
            y += 70
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=buf.getvalue())
    data = doc.tobytes()
    doc.close()
    return data


class MemoryWatcher:
    """Samples the resident memory of the server and all its children."""

    def __init__(self, root: psutil.Process):
        self.root = root
        self.peak = 0
        self.peak_at = ""
        self.stage = "startup"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            total = 0
            try:
                procs = [self.root] + self.root.children(recursive=True)
            except psutil.Error:
                procs = []
            for p in procs:
                try:
                    total += p.memory_info().rss
                except psutil.Error:
                    pass
            if total > self.peak:
                self.peak = total
                self.peak_at = self.stage
            time.sleep(1.0)


def find_server(timeout: float) -> psutil.Process:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                cmd = " ".join(p.info["cmdline"] or [])
            except psutil.Error:
                continue
            if "server.main" in cmd and "python" in (p.info["name"] or "").lower():
                # The launcher may go through a parent; the server is the
                # process that actually runs server.main without children of
                # the same command line above it.
                return p
        time.sleep(1.0)
    raise SystemExit("FAIL: no server.main process appeared")


def wait_ready(base: str, timeout: float) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            health = requests.get(f"{base}/api/health", timeout=10).json()
        except requests.RequestException:
            time.sleep(3)
            continue
        components = health.get("components", {})
        state = (
            health.get("downloading"),
            components.get("ner", {}).get("installing"),
            components.get("warmup", {}).get("warming"),
        )
        if state != last:
            print(f"[{time.strftime('%H:%M:%S')}] downloading={state[0]} "
                  f"ner_installing={state[1]} warming={state[2]}", flush=True)
            last = state
        if health.get("ready"):
            return health
        errors = [health.get("error"), components.get("ner", {}).get("error"),
                  components.get("warmup", {}).get("error")]
        if any(errors):
            raise SystemExit(f"FAIL: preparation error: {errors}")
        time.sleep(3)
    raise SystemExit("FAIL: the app never reported ready")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7860)
    ap.add_argument("--budget-gb", type=float, default=0.0)
    ap.add_argument("--ready-timeout", type=float, default=2700)
    ap.add_argument("--out", default="e2e-report.json")
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    report: dict = {"ok": False}
    failures: list[str] = []

    server = find_server(timeout=180)
    print(f"server process: pid={server.pid} {' '.join(server.cmdline())}")
    watcher = MemoryWatcher(server)
    watcher.start()

    t0 = time.time()
    health = wait_ready(base, args.ready_timeout)
    report["ready_seconds"] = round(time.time() - t0, 1)
    report["device"] = health.get("device_label")
    print(f"ready after {report['ready_seconds']}s on {report['device']}")

    watcher.stage = "text"
    t = time.time()
    r = requests.post(f"{base}/api/redact", timeout=600, json={
        "text": "Comparece D. Juan Pérez Martínez, con DNI 12345678Z.",
        "mode": "balanced",
    })
    r.raise_for_status()
    text_result = r.json()
    report["text_seconds"] = round(time.time() - t, 1)
    print("text:", text_result.get("redacted_text"))
    if "12345678Z" in text_result.get("redacted_text", ""):
        failures.append("text: DNI not redacted")
    if "Pérez" in text_result.get("redacted_text", ""):
        failures.append("text: name not redacted")

    watcher.stage = "scanned-pdf"
    t = time.time()
    r = requests.post(
        f"{base}/api/redact-file",
        files={"file": ("escaneado_sintetico.pdf", make_scanned_pdf(),
                        "application/pdf")},
        data={"mode": "balanced"},
        timeout=1800,
    )
    r.raise_for_status()
    pdf_result = r.json()
    report["pdf_seconds"] = round(time.time() - t, 1)
    report["pdf_timings"] = pdf_result.get("timings")
    found = {(s["label"], s["text"]) for s in pdf_result.get("detected_spans", [])}
    report["pdf_spans"] = sorted(f"{label}: {text}" for label, text in found)
    print("pdf spans:", *report["pdf_spans"], sep="\n  ")
    labels = {label for label, _ in found}
    texts = " ".join(text for _, text in found)
    for dni in ("12345678Z", "87654321X"):
        if dni not in texts.replace(" ", ""):
            failures.append(f"pdf: DNI {dni} not detected (OCR or recognizer)")
    if "NOMBRE" not in labels:
        failures.append("pdf: no person name detected")
    token = pdf_result.get("download_token")
    if not token:
        failures.append("pdf: no redacted file produced")
    else:
        dl = requests.get(f"{base}/api/download/{token}", timeout=120)
        if dl.status_code != 200 or not dl.content.startswith(b"%PDF"):
            failures.append(f"pdf: download failed ({dl.status_code})")

    watcher.stage = "idle"
    time.sleep(3)
    watcher.stop()
    peak_gb = watcher.peak / 1024 ** 3
    report["peak_memory_gb"] = round(peak_gb, 2)
    report["peak_memory_stage"] = watcher.peak_at
    print(f"peak memory of the app: {peak_gb:.2f} GB (during {watcher.peak_at})")
    if args.budget_gb > 0 and peak_gb > args.budget_gb:
        failures.append(
            f"memory: peak {peak_gb:.2f} GB exceeds the {args.budget_gb} GB budget"
        )

    try:
        requests.post(f"{base}/api/shutdown", timeout=10)
    except requests.RequestException:
        pass

    report["failures"] = failures
    report["ok"] = not failures
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    if failures:
        print("FAIL:", *failures, sep="\n  ")
        return 1
    print("OK: installed build passed the end-to-end check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
