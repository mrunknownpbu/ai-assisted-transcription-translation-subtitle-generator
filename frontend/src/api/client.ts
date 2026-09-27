import type {
  AudioStreamRecommendation,
  BrowseResponse,
  DeleteGlossaryEntityRequest,
  DeleteGlossaryEntityResponse,
  Job,
  JobListResponse,
  JobRequest,
  LanguagesResponse,
  MediaMetadata,
  PromoteGlossaryEntityRequest,
  PromoteGlossaryEntityResponse,
  QueueCounts,
  RetryRequest,
  SeriesDetailResponse,
  SeriesListResponse,
  SrtEditRequest,
  SrtEditResponse,
  SrtEditorResponse,
  SrtTranslationRequest,
  UpdateGlossaryEntityRequest,
  UpdateGlossaryEntityResponse,
  UploadSrtResponse,
} from "./types";

export class ApiError extends Error {}

// SUBTITLE_AI_API_KEY (api.py's require_api_key) guards cancel/retry/
// delete, glossary edits and the SRT editor. Until 2026-09-28 the UI never
// sent it, so enabling the key disabled the UI's own buttons with a 401.
// Now a 401 asks for the key once, keeps it in this browser, and retries.
// With no key configured server-side nothing changes: no header, no prompt.
export const API_KEY_STORAGE = "subtitle-ai.api-key";

export function storedApiKey(): string | null {
  try {
    return localStorage.getItem(API_KEY_STORAGE);
  } catch {
    return null;
  }
}

function storeApiKey(key: string | null) {
  try {
    if (key) localStorage.setItem(API_KEY_STORAGE, key);
    else localStorage.removeItem(API_KEY_STORAGE);
  } catch {
    // storage blocked (private window etc.) -- the key just isn't remembered
  }
}

function withApiKey(headers: HeadersInit | undefined): HeadersInit {
  const key = storedApiKey();
  return key ? { ...headers, "X-API-Key": key } : { ...headers };
}

// One prompt per 401, then one retry. A wrong key is forgotten so the next
// action asks again instead of failing silently forever.
async function fetchWithApiKey(path: string, init: RequestInit): Promise<Response> {
  const res = await fetch(path, { ...init, headers: withApiKey(init.headers) });
  if (res.status !== 401) return res;
  storeApiKey(null);
  const entered = window.prompt("This action needs the subtitle-ai API key (SUBTITLE_AI_API_KEY):");
  if (!entered) return res;
  storeApiKey(entered.trim());
  const retried = await fetch(path, { ...init, headers: withApiKey(init.headers) });
  if (retried.status === 401) storeApiKey(null);
  return retried;
}

async function parse<T>(res: Response): Promise<T> {
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    // no/invalid JSON body -- fine for e.g. a bare 204
  }
  if (!res.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : res.statusText;
    throw new ApiError(detail);
  }
  return body as T;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return parse<T>(
    await fetchWithApiKey(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    }),
  );
}

const qs = (params: Record<string, string | number | undefined>) => {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) usp.set(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
};

export const api = {
  browse: (path: string, fileType: "video" | "srt" = "video") =>
    request<BrowseResponse>(`/api/browse${qs({ path, file_type: fileType })}`),
  media: (path: string) => request<MediaMetadata>(`/api/media${qs({ path })}`),
  languages: () => request<LanguagesResponse>("/api/languages"),
  createSrtTranslationJob: (body: SrtTranslationRequest) =>
    request<{ job: Job }>("/api/srt-translations", { method: "POST", body: JSON.stringify(body) }),
  uploadSrt: async (file: File) => {
    // Deliberately bypasses request<T>()'s JSON Content-Type header --
    // the browser must set its own multipart boundary for FormData.
    const form = new FormData();
    form.append("file", file);
    return parse<UploadSrtResponse>(
      await fetchWithApiKey("/api/srt-uploads", { method: "POST", body: form }),
    );
  },
  audioStreams: (path: string) =>
    request<AudioStreamRecommendation>(`/api/audio-streams${qs({ path })}`),
  createJob: (body: JobRequest) =>
    request<{ job: Job }>("/api/jobs", { method: "POST", body: JSON.stringify(body) }),
  listJobs: (params: { status?: string; limit?: number; offset?: number } = {}) =>
    request<JobListResponse>(`/api/jobs${qs(params)}`),
  getJob: (id: string) => request<Job>(`/api/jobs/${id}`),
  queueCounts: () => request<QueueCounts>("/api/queue"),
  cancelJob: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  retryJob: (id: string, body: RetryRequest = {}) =>
    request<{ job: Job }>(`/api/jobs/${id}/retry`, { method: "POST", body: JSON.stringify(body) }),
  deleteJob: (id: string) => request<{ deleted: string }>(`/api/jobs/${id}`, { method: "DELETE" }),
  listSeries: () => request<SeriesListResponse>("/api/series"),
  seriesDetail: (tvdbId: number) => request<SeriesDetailResponse>(`/api/series/${tvdbId}`),
  promoteGlossaryEntity: (tvdbId: number, body: PromoteGlossaryEntityRequest) =>
    request<PromoteGlossaryEntityResponse>(`/api/series/${tvdbId}/glossary/promote`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateGlossaryEntity: (tvdbId: number, body: UpdateGlossaryEntityRequest) =>
    request<UpdateGlossaryEntityResponse>(`/api/series/${tvdbId}/glossary/update`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteGlossaryEntity: (tvdbId: number, body: DeleteGlossaryEntityRequest) =>
    request<DeleteGlossaryEntityResponse>(`/api/series/${tvdbId}/glossary/delete`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getJobSrt: (id: string) => request<SrtEditorResponse>(`/api/jobs/${id}/srt`),
  updateJobSrt: (id: string, body: SrtEditRequest) =>
    request<SrtEditResponse>(`/api/jobs/${id}/srt`, { method: "PUT", body: JSON.stringify(body) }),
};
