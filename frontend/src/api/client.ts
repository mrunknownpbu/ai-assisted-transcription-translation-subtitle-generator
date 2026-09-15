import type {
  AppSettings, EvaluationReport, GlossaryEntity, HardwareProfile, Job, JobLogs, LanguageDetection, LibraryEntry,
  MediaFile, Output, ReferenceOptions, StorageInfo,
} from "../types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }), ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  uploadMedia: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<MediaFile>("/media/upload", { method: "POST", body: form });
  },
  listMedia: () => request<MediaFile[]>("/media"),
  getMedia: (id: string) => request<MediaFile>(`/media/${id}`),
  getReferenceOptions: (mediaId: string) => request<ReferenceOptions>(`/media/${mediaId}/reference-options`),
  selectStream: (mediaId: string, streamIndex: number) =>
    request<MediaFile>(`/media/${mediaId}/select-stream`, {
      method: "POST",
      body: JSON.stringify({ stream_index: streamIndex }),
    }),
  detectLanguage: (mediaId: string, streamId: string) =>
    request<LanguageDetection>(`/media/${mediaId}/streams/${streamId}/detect-language`, { method: "POST" }),
  browseLibrary: (path: string = "") =>
    request<LibraryEntry[]>(`/library/browse?path=${encodeURIComponent(path)}`),
  registerLibraryMedia: (path: string) =>
    request<MediaFile>("/media/register", { method: "POST", body: JSON.stringify({ path }) }),

  createJob: (payload: {
    media_file_id: string;
    audio_stream_id: string;
    source_language: string | null;
    target_languages: string[];
    output_formats: string[];
    priority?: number;
    glossary_entities?: GlossaryEntity[] | null;
    tvdb_id?: number | null;
  }) => request<Job>("/jobs", { method: "POST", body: JSON.stringify(payload) }),
  createDirectTranslationJob: (payload: {
    file: File;
    target_languages: string[];
    output_formats: string[];
    source_language?: string | null;
    glossary_entities?: GlossaryEntity[] | null;
    tvdb_id?: number | null;
  }) => {
    const form = new FormData();
    form.append("file", payload.file);
    form.append("target_languages", payload.target_languages.join(","));
    form.append("output_formats", payload.output_formats.join(","));
    if (payload.source_language) form.append("source_language", payload.source_language);
    if (payload.glossary_entities?.length) form.append("glossary_entities", JSON.stringify(payload.glossary_entities));
    if (payload.tvdb_id) form.append("tvdb_id", String(payload.tvdb_id));
    return request<Job>("/jobs/direct-translation", { method: "POST", body: form });
  },
  listJobs: (status?: string) => request<Job[]>(`/jobs${status ? `?status=${status}` : ""}`),
  getJob: (id: string) => request<Job>(`/jobs/${id}`),
  cancelJob: (id: string) => request<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
  pauseJob: (id: string) => request<Job>(`/jobs/${id}/pause`, { method: "POST" }),
  resumeJob: (id: string) => request<Job>(`/jobs/${id}/resume`, { method: "POST" }),
  retryJob: (id: string) => request<Job>(`/jobs/${id}/retry`, { method: "POST" }),
  reorderJob: (id: string, priority: number) =>
    request<Job>(`/jobs/${id}/priority`, { method: "PATCH", body: JSON.stringify({ priority }) }),
  decideExistingOutput: (id: string, decision: "keep" | "replace") =>
    request<Job>(`/jobs/${id}/existing-output-decision`, { method: "POST", body: JSON.stringify({ decision }) }),
  getJobLogs: (id: string) => request<JobLogs>(`/jobs/${id}/logs`),
  getJobOutputs: (id: string) => request<Output[]>(`/jobs/${id}/outputs`),
  evaluateJob: (id: string, payload: {
    mode?: "translation" | "transcription"; target_language?: string; reference_srt_path?: string;
    embedded_stream_index?: number; reference_archive_path?: string;
  }) =>
    request<EvaluationReport>(`/jobs/${id}/evaluate`, { method: "POST", body: JSON.stringify(payload) }),
  listEvaluations: (id: string) => request<EvaluationReport[]>(`/jobs/${id}/evaluations`),
  browseReferences: (path: string = "") =>
    request<LibraryEntry[]>(`/references/browse?path=${encodeURIComponent(path)}`),

  getHardware: () => request<HardwareProfile>("/hardware"),
  getStorage: () => request<StorageInfo>("/hardware/storage"),
  getSettings: () => request<AppSettings>("/settings"),

  downloadOutputUrl: (outputId: number) => `/api/outputs/${outputId}/download`,
};

export function subscribeToJobProgress(
  jobId: string,
  onUpdate: (data: { id: string; status: string; current_stage: string | null; progress_pct: number; error_message: string | null }) => void,
): () => void {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${protocol}://${window.location.host}/ws/jobs/${jobId}`);
  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (!data.error) onUpdate(data);
  };
  return () => ws.close();
}
