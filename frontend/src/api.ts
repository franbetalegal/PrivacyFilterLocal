// Typed client for the Privacy Filter FastAPI backend.

export interface DetectedSpan {
  label: string;
  start: number;
  end: number;
  text: string;
  placeholder: string;
}

export interface RedactTextResult {
  redacted_text: string;
  detected_spans: DetectedSpan[];
  summary?: Record<string, unknown>;
  warning?: string | null;
  elapsed: number;
  empty?: boolean;
}

export interface RedactFileResult {
  detected_spans: DetectedSpan[];
  summary?: Record<string, unknown>;
  warning?: string | null;
  elapsed: number;
  download_token: string | null;
  download_name: string | null;
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

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function getVersion(): Promise<string> {
  const data = await jsonOrThrow<{ version: string }>(await fetch("/api/version"));
  return data.version;
}

export async function getHealth(): Promise<{ model_loaded: boolean; loading: boolean }> {
  return jsonOrThrow(await fetch("/api/health"));
}

export async function redactText(text: string): Promise<RedactTextResult> {
  return jsonOrThrow(
    await fetch("/api/redact", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }),
  );
}

export async function redactFile(file: File): Promise<RedactFileResult> {
  const form = new FormData();
  form.append("file", file);
  return jsonOrThrow(await fetch("/api/redact-file", { method: "POST", body: form }));
}

export function downloadUrl(token: string): string {
  return `/api/download/${token}`;
}

export async function getUpdates(): Promise<UpdatesInfo> {
  return jsonOrThrow(await fetch("/api/updates"));
}

export async function installAppUpdate(): Promise<{ status: string; message: string }> {
  return jsonOrThrow(await fetch("/api/updates/app", { method: "POST" }));
}

export async function installModelUpdate(): Promise<{ status: string; message: string }> {
  return jsonOrThrow(await fetch("/api/updates/model", { method: "POST" }));
}
