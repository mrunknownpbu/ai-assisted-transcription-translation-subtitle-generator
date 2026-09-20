// Hand-written to mirror subtitle_ai/api.py's actual response shapes
// exactly (no Pydantic response models / OpenAPI codegen in v1 -- see
// the plan's note on this simplification). Keep in sync with api.py by
// hand; jobstore.py's _row_to_dict()/SCHEMA and qc/types.py's JobQc are
// the source of truth for the Job/Qc shapes below.

export type JobStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "skipped"
  | "cancelled";

export interface QcFinding {
  category: string;
  reason: string;
  confidence: number;
  index: number;
  evidence: Record<string, unknown>;
}

export interface QcStage {
  stage: string;
  population: number;
  flagged: number;
  retried: number;
  improved: number;
  unresolved: number;
  findings: QcFinding[];
}

export type JobQc = Partial<Record<string, QcStage>>;

export interface LogEntry {
  time: number;
  message: string;
}

export type JobType = "video" | "srt_translation";

export interface Job {
  id: string;
  job_type: JobType;
  video_path: string;
  source_srt_path: string | null;
  destination_srt_path: string | null;
  source_is_uploaded: boolean;
  status: JobStatus;
  stage: string;
  progress: number;
  source_lang: string | null;
  target_lang: string;
  detected_language: string | null;
  language_confidence: number | null;
  source_language_mode: "AUTO" | "MANUAL";
  requested_audio_stream: number | null;
  selected_audio_stream: number | null;
  embedded_stream_language: string | null;
  stream_selection_mode: "AUTO" | "MANUAL";
  selected_stream_reason: string | null;
  overwrite_original: boolean;
  overwrite_english: boolean;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  updated_at: number;
  error: string | null;
  error_category: string | null;
  outputs: string[];
  qc: JobQc;
  cancel_requested: boolean;
  retry_of_job_id: string | null;
  attempt: number;
  log: LogEntry[];
  tvdb_id: number | null;
  elapsed_seconds: number;
  // Count of high-confidence QC findings (entity_error/hallucination
  // categories, or any finding >=0.7 confidence) worth a human's
  // attention -- advisory only, never blocks completion. See
  // qc/types.py's JobQc.needs_review_count() docstring for why.
  needs_review: number;
}

export interface JobListResponse {
  jobs: Job[];
  total: number;
}

export type QueueCounts = Record<
  "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "SKIPPED" | "CANCELLED" | "ALL",
  number
>;

export interface BrowseEntry {
  name: string;
  path: string;
  type: "directory" | "video" | "srt";
  size?: number;
}

export interface BrowseResponse {
  path: string;
  entries: BrowseEntry[];
}

export interface AudioStream {
  index: number;
  codec: string | null;
  codec_long_name: string | null;
  channels: number | null;
  channel_layout: string | null;
  sample_rate: number | null;
  bit_rate: number | null;
  duration: number | null;
  title: string | null;
  language: string | null;
  handler_name: string | null;
  default: boolean;
  forced: boolean;
  hearing_impaired: boolean;
  visual_impaired: boolean;
  commentary: boolean;
  exclusion_reason: string | null;
}

export interface MediaMetadata {
  duration: number;
  audio_tracks: AudioStream[];
  path: string;
  filename: string;
  size: number;
  existing_subtitles: string[];
}

export interface StreamAlternate {
  index: number;
  language: string | null;
  confidence: number | null;
  reason: string;
}

export interface StreamRanked extends StreamAlternate {
  score: number;
}

export interface AudioStreamRecommendation {
  streams: AudioStream[];
  recommended_index: number | null;
  recommended_language: string | null;
  recommended_confidence: number | null;
  reason: string;
  alternates: StreamAlternate[];
  ranked: StreamRanked[];
}

export interface JobRequest {
  video_path: string;
  source_lang?: string;
  target_lang?: string;
  audio_stream_index?: number | null;
  overwrite_original?: boolean;
  overwrite_english?: boolean;
}

export interface RetryRequest {
  overwrite_original?: boolean;
  overwrite_english?: boolean;
  source_lang?: string | null;
  audio_stream_index?: number | null;
}

export interface SeriesSummary {
  tvdb_id: number | null;
  title: string | null;
  total: number;
  counts: Record<string, number>;
  last_updated: number;
}

export interface SeriesListResponse {
  series: SeriesSummary[];
}

export interface GlossaryEntity {
  canonical: string;
  surface_forms: string[];
}

export interface AutoSuggestion {
  canonical: string;
  aliases: string[];
  protected: boolean;
  occurrences: number;
  distinct_episodes: number;
}

export interface SeriesDetailResponse {
  tvdb_id: number;
  title: string | null;
  jobs: Job[];
  manual_glossary: GlossaryEntity[];
  auto_suggestions: AutoSuggestion[];
}

export interface PromoteGlossaryEntityRequest {
  canonical: string;
  aliases?: string[];
}

export interface PromoteGlossaryEntityResponse {
  manual_glossary: GlossaryEntity[];
}

export interface UpdateGlossaryEntityRequest {
  original_canonical: string;
  canonical: string;
  aliases?: string[];
}

export interface UpdateGlossaryEntityResponse {
  manual_glossary: GlossaryEntity[];
}

export interface DeleteGlossaryEntityRequest {
  canonical: string;
}

export interface DeleteGlossaryEntityResponse {
  manual_glossary: GlossaryEntity[];
}

export interface JobChangedEvent {
  type: "job_changed";
  job_id: string;
}

export interface SrtTranslationRequest {
  // REQUIRED -- the destination (<video stem>.en.srt) and tvdb_id are both
  // derived from this server-side; there is no separate destination field.
  video_path: string;
  // Exactly one of these two must be given.
  source_srt_path?: string | null;
  source_upload_id?: string | null;
  source_lang?: string;
  target_lang?: string;
  overwrite_english?: boolean;
}

export interface UploadSrtResponse {
  upload_id: string;
  filename: string;
}

export interface LanguagesResponse {
  languages: string[];
}
