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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
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
    const res = await fetch("/api/srt-uploads", { method: "POST", body: form });
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      // no/invalid JSON body
    }
    if (!res.ok) {
      const detail =
        body && typeof body === "object" && "detail" in body
          ? String((body as { detail: unknown }).detail)
          : res.statusText;
      throw new ApiError(detail);
    }
    return body as UploadSrtResponse;
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
