export interface LibraryEntry {
  name: string;
  path: string;
  is_dir: boolean;
}

export interface AudioStream {
  id: string;
  stream_index: number;
  codec: string;
  channels: number;
  sample_rate: number;
  duration_s: number;
  embedded_language_tag: string | null;
  dialogue_score: number | null;
  dialogue_rank: number | null;
  dialogue_score_breakdown: Record<string, number> | null;
  selected_by: "auto" | "manual" | null;
}

export interface MediaFile {
  id: string;
  filename: string;
  container_format: string;
  size_bytes: number;
  inspected_at: string;
  audio_streams: AudioStream[];
  tvdb_id: number | null;
}

export interface LanguageDetection {
  detected_language: string;
  confidence: number;
  method: string;
  candidates: [string, number][];
}

export interface GlossaryEntity {
  canonical: string;
  aliases: string[];
}

export interface Job {
  id: string;
  job_type: "audio_pipeline" | "direct_translation";
  media_file_id: string | null;
  audio_stream_id: string | null;
  input_filename: string | null;
  display_filename: string | null;
  source_language: string | null;
  target_languages: string[];
  output_formats: string[];
  status: "queued" | "running" | "paused" | "needs_decision" | "canceled" | "failed" | "done";
  current_stage: string | null;
  progress_pct: number;
  priority: number;
  retry_count: number;
  max_retries: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface Output {
  id: number;
  format: string;
  target_language: string;
  file_path: string;
  provenance_json: Record<string, unknown>;
  created_at: string;
}

export interface HardwareProfile {
  vendor: "nvidia" | "rocm" | "cpu";
  gpu_count: number;
  gpus: { index: number; name: string; total_vram_mb: number }[];
  cpu_cores: number;
  total_ram_mb: number;
  fallback_reason: string | null;
}

export interface StorageInfo {
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
}

export interface CueComparison {
  hypothesis_index: number;
  start: number;
  end: number;
  hypothesis_text: string;
  reference_text: string;
  chrf: number;
  word_f1: number;
}

export interface EmbeddedSubtitleStream {
  index: number;
  language: string | null;
  codec_name: string | null;
}

export interface SiblingSubtitleFile {
  name: string;
  path: string;
}

export interface ReferenceOptions {
  embedded_subtitle_streams: EmbeddedSubtitleStream[];
  sibling_subtitle_files: SiblingSubtitleFile[];
}

export interface EvaluationReport {
  id: string;
  job_id: string;
  target_language: string;
  reference_source: string;
  cue_count: number;
  matched_count: number;
  coverage: number;
  mean_chrf: number;
  mean_word_f1: number;
  per_cue: CueComparison[];
  created_at: string;
}

export interface AppSettings {
  whisper_model_size: string;
  nllb_model_name: string;
  max_concurrent_gpu_jobs: number;
  library_configured: boolean;
  tvdb_configured: boolean;
}

export interface JobLogs {
  stages: {
    stage_name: string;
    status: string;
    started_at: string;
    finished_at: string | null;
    log: string | null;
    provenance: Record<string, unknown> | null;
  }[];
  suppressions: {
    segment_id: string;
    reason: string;
    method: string;
    threshold: Record<string, unknown>;
    decision: string;
  }[];
  qc_reports: {
    target_language: string | null;
    passed: boolean;
    findings: { check: string; passed: boolean; reason: string; cue_id: string | null }[];
  }[];
}
