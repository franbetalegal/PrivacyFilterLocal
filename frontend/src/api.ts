import { renderErrorDetail, type MessageRef } from './messages';
// Typed client for the Privacy Filter FastAPI backend.

export interface DetectedSpan {
  label: string;
  start: number;
  end: number;
  text: string;
  placeholder: string;
}

/** Precision ↔ recall operating point. Higher precision on the left,
 *  higher recall on the right. Unknown values fall back server-side to
 *  ``balanced``. */
export type Mode = "conservative" | "balanced" | "aggressive";
export const MODES: Mode[] = ["conservative", "balanced", "aggressive"];
export const MODE_LABEL: Record<Mode, string> = {
  conservative: "Conservador (menos falsos positivos)",
  balanced: "Equilibrado (por defecto)",
  aggressive: "Máxima cobertura (menos fugas)",
};

export interface RedactTextResult {
  redacted_text: string;
  detected_spans: DetectedSpan[];
  summary?: Record<string, unknown>;
  warnings?: MessageRef[];
  elapsed: number;
  empty?: boolean;
  mode?: Mode;
}

/** Per-stage wall time in seconds, as measured by the server. */
export interface StageTimings {
  extract: number;
  detect: number;
  redact: number;
  verify: number;
  total: number;
}

export interface RedactFileResult {
  detected_spans: DetectedSpan[];
  summary?: Record<string, unknown>;
  warnings?: MessageRef[];
  elapsed: number;
  download_token: string | null;
  download_name: string | null;
  /** Optional .md rendering of the redacted output, present only when the
   *  caller passed ``alsoMarkdown``. Meant for pasting into an LLM: fewer
   *  tokens than a PDF and explicit structure (headings, lists, tables). */
  markdown_token?: string | null;
  markdown_name?: string | null;
  mode?: Mode;
  leaked_pii_count?: number;
  timings?: StageTimings;
  /** False when the leak check could not inspect the output (scanned source).
   *  An empty leak list must NOT be read as "clean" in that case. */
  verified?: boolean;
}

export interface ApplyRedactionResult {
  download_token: string | null;
  download_name: string | null;
  markdown_token?: string | null;
  markdown_name?: string | null;
  applied_span_count: number;
  leaked_pii_count: number;
  warnings?: MessageRef[];
  elapsed?: number;
  timings?: StageTimings;
  verified?: boolean;
  captured?: { added: boolean; total: number; reason?: string } | null;
}

export interface AppUpdateInfo {
  update_available: boolean;
  current_version: string;
  latest_version: string;
  changelog: string;
  published_date: string;
  error: string | null;
}

export interface ModelUpdateInfo {
  update_available: boolean;
  current_date: string | null;
  latest_date: string | null;
  error: string | null;
}

export interface UpdatesInfo {
  app: AppUpdateInfo;
  model: ModelUpdateInfo;
}

/** Texto que ve el usuario cuando el navegador no consigue ni hablar con el
 *  servidor local. Sustituye al «Failed to fetch» del navegador, que no dice
 *  qué ha pasado ni qué hacer. */
export const SERVER_UNREACHABLE_MESSAGE =
  "No se ha podido contactar con el servidor local de la aplicación. Puede " +
  "que se haya cerrado o que el equipo se haya quedado sin memoria con un " +
  "documento muy grande. Recargue la página; si sigue sin responder, vuelva " +
  "a abrir la aplicación y, si el problema se repite, descargue el " +
  "diagnóstico desde la cabecera y envíelo a soporte.";

/** Raised when the request never reached the server (or the connection died
 *  mid-request). Distinct from an HTTP error, which the server did answer. */
export class ServerUnreachableError extends Error {
  constructor() {
    super(SERVER_UNREACHABLE_MESSAGE);
    this.name = "ServerUnreachableError";
  }
}

/** `fetch` that turns a network-level failure into a readable error.
 *  A cancellation (AbortError) is passed through untouched so callers can
 *  still tell "the user pressed Cancel" apart from "the server is gone". */
async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch (e) {
    if (isAbort(e)) throw e;
    throw new ServerUnreachableError();
  }
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    // The backend answers with {code, params}; the Spanish sentence is built
    // here so every caller can show `error.message` directly.
    let detail: unknown = null;
    try {
      const body = await res.json();
      detail = body.detail ?? null;
    } catch {
      /* a non-JSON body leaves detail null and the status text is used */
    }
    throw new Error(renderErrorDetail(detail, res.statusText));
  }
  return res.json() as Promise<T>;
}

export async function getVersion(): Promise<string> {
  const data = await jsonOrThrow<{ version: string }>(await apiFetch("/api/version"));
  return data.version;
}

/** One spaCy NER model, as reported by /api/health. */
export interface NerModelStatus {
  name: string;
  present: boolean;
  /** "managed" (downloaded by the app), "pip" (installed in the venv), or null. */
  source: "managed" | "pip" | null;
  version: string | null;
  path: string | null;
  /** Newest version compatible with the installed spaCy, if the check ran. */
  latest: string | null;
}

/** State of every detection component. The app is only usable when the model
 *  and the NER layer are both in place: without the NER models, names written
 *  in caps are not detected and the result still looks like a clean document. */
export interface HealthComponents {
  opf: {
    available: boolean;
    loaded: boolean;
    downloading: boolean;
    download_pct: number;
    error: string | null;
    checkpoint_dir: string;
  };
  ner: {
    installing: boolean;
    install_pct: number;
    /** Model currently being downloaded, while ``installing``. */
    current: string | null;
    error: string | null;
    available: boolean;
    loaded: number;
    models: NerModelStatus[];
  };
  /** Tesseract. Only affects scanned documents, so it does not gate the app. */
  ocr: { available: boolean; binary: string | null };
  /** Models loaded in memory and exercised once. The app is not ready before. */
  warmup?: { warming: boolean; warm: boolean; error: string | null };
}

export interface Health {
  model_loaded: boolean;
  loading: boolean;
  downloading: boolean;
  download_pct: number;
  error: string | null;
  /** Decided by the server so there is one definition of "can anonymise now". */
  ready?: boolean;
  components?: HealthComponents;
  /** Detected inference device: cpu / cuda / mps. */
  device?: string;
  /** Human-readable device label ("mps (Apple Silicon GPU via Metal)"). */
  device_label?: string;
  /** Random id of the server process. Changes only when the backend restarts. */
  instance?: string;
}

export async function getHealth(): Promise<Health> {
  return jsonOrThrow(await apiFetch("/api/health"));
}

/** Whether the app may be used. Trusts the server's own verdict; the older
 *  fields are the fallback for a backend that predates it. */
export function isReady(health: Health | null): boolean {
  if (health === null) return false;
  if (typeof health.ready === "boolean") return health.ready;
  return !health.downloading && !health.error;
}

export const DIAGNOSTICS_URL = "/api/diagnostics";

export async function shutdown(): Promise<void> {
  await apiFetch("/api/shutdown", { method: "POST" });
}

export async function redactText(
  text: string,
  mode: Mode = "balanced",
  signal?: AbortSignal,
): Promise<RedactTextResult> {
  return jsonOrThrow(
    await apiFetch("/api/redact", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, mode }),
      signal,
    }),
  );
}

export async function redactFile(
  file: File,
  mode: Mode = "balanced",
  signal?: AbortSignal,
  alsoMarkdown = false,
): Promise<RedactFileResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("mode", mode);
  if (alsoMarkdown) form.append("also_markdown", "true");
  return jsonOrThrow(
    await apiFetch("/api/redact-file", { method: "POST", body: form, signal }),
  );
}

/** Re-apply the user's curated span list without running detection again.
 *  Used by the human-in-the-loop review flow: the caller re-uploads the
 *  same file plus the spans that survived their review, and the server
 *  produces a fresh redacted download that reflects exactly those spans. */
export async function applyRedaction(
  file: File,
  spans: DetectedSpan[],
  signal?: AbortSignal,
  saveExample = false,
  alsoMarkdown = false,
): Promise<ApplyRedactionResult> {
  const form = new FormData();
  form.append("file", file);
  form.append("spans_json", JSON.stringify(spans));
  form.append("save_example", saveExample ? "true" : "false");
  if (alsoMarkdown) form.append("also_markdown", "true");
  return jsonOrThrow(
    await apiFetch("/api/redact-file/apply", {
      method: "POST",
      body: form,
      signal,
    }),
  );
}

/** True if the error is a fetch abort (user pressed Cancel). */
export function isAbort(e: unknown): boolean {
  return e instanceof DOMException && e.name === "AbortError";
}

export function downloadUrl(token: string): string {
  return `/api/download/${token}`;
}

// --- Custom dictionary -----------------------------------------------------

export type MatchMode = "smart" | "exact" | "regex";

export interface DictEntry {
  id: string;
  term: string;
  label: string;
  match: MatchMode;
  enabled: boolean;
}

export interface DictionaryInfo {
  terms: DictEntry[];
  labels: string[];
  match_modes: MatchMode[];
}

export interface ImportResult {
  added: number;
  skipped: number;
  invalid: number;
}

export async function getDictionary(): Promise<DictionaryInfo> {
  return jsonOrThrow(await apiFetch("/api/dictionary"));
}

export async function addDictEntry(
  entry: Omit<DictEntry, "id">,
): Promise<DictEntry> {
  return jsonOrThrow(
    await apiFetch("/api/dictionary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(entry),
    }),
  );
}

export async function updateDictEntry(
  id: string,
  patch: Partial<Omit<DictEntry, "id">>,
): Promise<DictEntry> {
  return jsonOrThrow(
    await apiFetch(`/api/dictionary/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  );
}

export async function deleteDictEntry(id: string): Promise<void> {
  await jsonOrThrow(await apiFetch(`/api/dictionary/${id}`, { method: "DELETE" }));
}

export async function importDictionary(
  terms: unknown[],
): Promise<ImportResult> {
  return jsonOrThrow(
    await apiFetch("/api/dictionary/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ terms }),
    }),
  );
}

export const DICTIONARY_EXPORT_URL = "/api/dictionary/export";

// --- MarkItDown without anonymization -------------------------------------

export interface MarkdownOnlyResult {
  download_token: string;
  download_name: string;
  elapsed: number;
  char_count: number;
}

/** Convert a document to Markdown without running PII detection. The output
 *  is NOT anonymized: the caller must warn the user before letting them
 *  share/download the result. */
export async function convertToMarkdown(
  file: File,
  signal?: AbortSignal,
): Promise<MarkdownOnlyResult> {
  const form = new FormData();
  form.append("file", file);
  return jsonOrThrow(
    await apiFetch("/api/markdown", { method: "POST", body: form, signal }),
  );
}

// --- Gold set + evaluation (Phase 7) --------------------------------------

export interface DatasetStats {
  examples: number;
  spans: number;
  by_label: Record<string, number>;
  path: string;
}

export interface LabelMetrics {
  precision: number;
  recall: number;
  f1: number;
  pred: number;
  gold: number;
}

export interface OverallMetrics {
  precision: number;
  recall: number;
  f1: number;
  tp: number;
  fp: number;
  fn: number;
}

export interface EvalReport {
  examples: number;
  gold_spans: number;
  pred_spans: number;
  mode: Mode;
  overall: OverallMetrics;
  exact: OverallMetrics;
  by_label: Record<string, LabelMetrics>;
  leaks: { example: number; text: string; label: string }[];
  leak_count: number;
  detail?: string;
}

export async function captureExample(
  text: string,
  spans: DetectedSpan[],
): Promise<{ added: boolean; total: number; reason?: string }> {
  return jsonOrThrow(
    await apiFetch("/api/dataset/capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, spans }),
    }),
  );
}

export async function getDatasetStats(): Promise<DatasetStats> {
  return jsonOrThrow(await apiFetch("/api/dataset/stats"));
}

export async function evaluateDataset(mode: Mode): Promise<EvalReport> {
  return jsonOrThrow(
    await apiFetch("/api/dataset/evaluate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    }),
  );
}

export async function getUpdates(): Promise<UpdatesInfo> {
  return jsonOrThrow(await apiFetch("/api/updates"));
}

export async function installAppUpdate(): Promise<{
  status: string;
  message: string;
  /** The backend is about to restart itself; wait for a new Health.instance. */
  restarting?: boolean;
}> {
  return jsonOrThrow(await apiFetch("/api/updates/app", { method: "POST" }));
}

export async function installModelUpdate(): Promise<{ status: string; message: string }> {
  return jsonOrThrow(await apiFetch("/api/updates/model", { method: "POST" }));
}
