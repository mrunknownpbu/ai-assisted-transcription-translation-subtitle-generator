import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type {
  DeleteGlossaryEntityRequest,
  JobRequest,
  PromoteGlossaryEntityRequest,
  RetryRequest,
  SrtEditRequest,
  SrtTranslationRequest,
  UpdateGlossaryEntityRequest,
} from "./types";

export function useBrowse(path: string, fileType: "video" | "srt" = "video") {
  return useQuery({
    queryKey: ["browse", path, fileType],
    queryFn: () => api.browse(path, fileType),
  });
}

export function useLanguages() {
  return useQuery({ queryKey: ["languages"], queryFn: api.languages });
}

export function useMedia(path: string | null) {
  return useQuery({
    queryKey: ["media", path],
    queryFn: () => api.media(path as string),
    enabled: path !== null,
  });
}

export function useAudioStreams(path: string | null) {
  return useQuery({
    queryKey: ["audio-streams", path],
    queryFn: () => api.audioStreams(path as string),
    enabled: false, // explicitly triggered by the "Analyze" button, not on selection
  });
}

export function useJobs(status: string) {
  // Real bug (2026-09-22): JobsPage's tab labels are UPPERCASE
  // ("QUEUED", "RUNNING", ...) for display, but the job store's status
  // column is lowercase ("queued", "running", ...) and jobstore.list()
  // does an exact SQL match -- 'WHERE status=?' with the raw, uppercase
  // value never matched a row, so every tab except ALL silently showed
  // "No jobs." regardless of how many jobs actually had that status
  // (visible in the header's own separately-computed counts, which DO
  // normalize case). Lowercasing here, at the one place a tab label
  // becomes a query param, fixes every tab without touching the labels
  // themselves or the server's case-sensitive comparison.
  const apiStatus = status === "ALL" ? undefined : status.toLowerCase();
  return useQuery({
    queryKey: ["jobs", status],
    queryFn: () => api.listJobs({ status: apiStatus, limit: 100 }),
  });
}

export function useJob(id: string | undefined) {
  return useQuery({
    queryKey: ["job", id],
    queryFn: () => api.getJob(id as string),
    enabled: id !== undefined,
  });
}

export function useQueueCounts() {
  return useQuery({ queryKey: ["queue"], queryFn: api.queueCounts });
}

export function useSeriesList() {
  return useQuery({ queryKey: ["series"], queryFn: api.listSeries });
}

export function useSeriesDetail(tvdbId: number) {
  return useQuery({ queryKey: ["series", tvdbId], queryFn: () => api.seriesDetail(tvdbId) });
}

export function usePromoteGlossaryEntity(tvdbId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PromoteGlossaryEntityRequest) => api.promoteGlossaryEntity(tvdbId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["series", tvdbId] }),
  });
}

export function useUpdateGlossaryEntity(tvdbId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: UpdateGlossaryEntityRequest) => api.updateGlossaryEntity(tvdbId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["series", tvdbId] }),
  });
}

export function useDeleteGlossaryEntity(tvdbId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DeleteGlossaryEntityRequest) => api.deleteGlossaryEntity(tvdbId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["series", tvdbId] }),
  });
}

export function useCreateJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: JobRequest) => api.createJob(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useCancelJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.cancelJob(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useRetryJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body?: RetryRequest }) => api.retryJob(id, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useCreateSrtTranslationJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SrtTranslationRequest) => api.createSrtTranslationJob(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useUploadSrt() {
  return useMutation({ mutationFn: (file: File) => api.uploadSrt(file) });
}

export function useDeleteJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteJob(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

// IMPROVEMENT_PLAN.md 4.2. `enabled` is caller-controlled (JobDetailPage
// only fetches once the "Edit subtitles" panel is actually opened) --
// most job views never need this, so it shouldn't fire alongside useJob().
export function useJobSrt(id: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["job-srt", id],
    queryFn: () => api.getJobSrt(id as string),
    enabled: enabled && id !== undefined,
  });
}

export function useUpdateJobSrt(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SrtEditRequest) => api.updateJobSrt(id, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["job-srt", id] }),
  });
}
