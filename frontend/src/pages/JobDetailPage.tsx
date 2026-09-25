import { Link, useParams } from "react-router-dom";

import { useJob } from "../api/hooks";
import { JobStatusBadge } from "../components/JobStatusBadge";
import { ProgressBar } from "../components/ProgressBar";
import { QcFindingsList } from "../components/QcFindingsList";
import { SrtEditor } from "../components/SrtEditor";
import { fmtElapsed, fmtPercent } from "../format";

export function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: job, isLoading } = useJob(id);

  if (isLoading) return <div>Loading&hellip;</div>;
  if (!job) return <div>Job not found.</div>;

  const isSrt = job.job_type === "srt_translation";

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{job.video_path}</h2>
        <JobStatusBadge status={job.status} />
      </div>
      {isSrt && (
        <div className="option-row">
          <span>Type</span>
          <strong>SRT Translation → {job.destination_srt_path}</strong>
        </div>
      )}
      {isSrt && (
        <div className="option-row">
          <span>Source subtitle</span>
          <strong>{job.source_is_uploaded ? "Uploaded from computer" : job.source_srt_path}</strong>
        </div>
      )}

      <div className="option-row">
        <span>Stage</span>
        <strong>{job.stage}</strong>
      </div>
      <div className="option-row">
        <span>Progress</span>
        <ProgressBar progress={job.progress} status={job.status} elapsedSeconds={job.elapsed_seconds} />
      </div>
      <div className="option-row">
        <span>Elapsed</span>
        <strong>{fmtElapsed(job.elapsed_seconds)}</strong>
      </div>
      <div className="option-row">
        <span>Source language</span>
        <strong>
          requested {job.source_lang} ({job.source_language_mode})
          {job.detected_language &&
            ` → detected ${job.detected_language} (${fmtPercent(job.language_confidence)})`}
        </strong>
      </div>
      <div className="option-row">
        <span>Target language</span>
        <strong>{job.target_lang}</strong>
      </div>
      {!isSrt && (
        <div className="option-row">
          <span>Audio stream</span>
          <strong>
            {job.selected_audio_stream ?? "auto"} ({job.stream_selection_mode})
            {job.embedded_stream_language && ` — embedded: ${job.embedded_stream_language}`}
            {job.selected_stream_reason && ` — ${job.selected_stream_reason}`}
          </strong>
        </div>
      )}
      {job.retry_of_job_id && (
        <div className="option-row">
          <span>Retry chain</span>
          <strong>
            attempt {job.attempt}, retried from <Link to={`/jobs/${job.retry_of_job_id}`}>{job.retry_of_job_id}</Link>
          </strong>
        </div>
      )}
      {job.outputs.length > 0 && (
        <div className="option-row">
          <span>Outputs</span>
          <strong>{job.outputs.join(", ")}</strong>
        </div>
      )}
      {job.error && (
        <div className="detail">
          <h3>Error {job.error_category ? `(${job.error_category})` : ""}</h3>
          <div className="error">{job.error}</div>
        </div>
      )}

      {job.status === "completed" && <SrtEditor jobId={job.id} />}

      <div className="detail">
        <h3>QC findings</h3>
        <QcFindingsList qc={job.qc} />
      </div>

      <div className="detail">
        <h3>Log</h3>
        <div className="log">
          {job.log.map((entry, i) => (
            <div key={i}>
              [{new Date(entry.time * 1000).toLocaleTimeString()}] {entry.message}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
