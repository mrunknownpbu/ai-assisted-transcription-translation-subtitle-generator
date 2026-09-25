import { Link } from "react-router-dom";

import { useCancelJob, useDeleteJob, useRetryJob } from "../api/hooks";
import { ApiError } from "../api/client";
import type { Job } from "../api/types";
import { fmtDateTime, fmtElapsed } from "../format";
import { useToast } from "./Toast";
import { JobStatusBadge } from "./JobStatusBadge";
import { ProgressBar } from "./ProgressBar";

const ACTIVE = new Set(["queued", "running"]);
const RETRYABLE = new Set(["completed", "failed", "cancelled", "skipped"]);
const DELETABLE = new Set(["completed", "failed", "cancelled", "skipped"]);

function qcSummary(job: Job): string {
  // needs_review surfaces only the subset of QC findings worth a
  // human's attention (see qc/types.py's JobQc.needs_review_count()) --
  // shown ahead of the generic flagged count so it isn't buried the
  // way every real bug found this session originally was.
  if (job.needs_review > 0) {
    return `⚠ ${job.needs_review} need${job.needs_review === 1 ? "s" : ""} review`;
  }
  const flagged = Object.values(job.qc).reduce((sum, stage) => sum + (stage?.flagged ?? 0), 0);
  return flagged > 0 ? `${flagged} flagged` : "clean";
}

export function JobTable({ jobs }: { jobs: Job[] }) {
  const cancelJob = useCancelJob();
  const retryJob = useRetryJob();
  const deleteJob = useDeleteJob();
  const { notify } = useToast();

  const handle = (promise: Promise<unknown>, verb: string) =>
    promise
      .then(() => notify(`${verb} succeeded.`))
      .catch((err) => notify(err instanceof ApiError ? err.message : `${verb} failed`, "error"));

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Type</th>
            <th>File</th>
            <th>Date/Time</th>
            <th>Status</th>
            <th>Stage</th>
            <th>Progress</th>
            <th>Elapsed</th>
            <th>QC flags</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {jobs.length === 0 && (
            <tr>
              <td className="empty" colSpan={9}>
                No jobs.
              </td>
            </tr>
          )}
          {jobs.map((job) => {
            const isSrt = job.job_type === "srt_translation";
            // An SRT-translation job is identified by the EPISODE it translates,
            // not its source file: an uploaded source is stored under an
            // internal <hash>.srt name that means nothing to the user.
            const fileIdentity = job.video_path;
            const fileTitle = isSrt
              ? `${job.video_path}\nSource: ${
                  job.source_is_uploaded ? "uploaded from computer" : job.source_srt_path ?? "unknown"
                }`
              : job.video_path;
            return (
            <tr key={job.id} className={job.status === "failed" ? "status-failed" : ""}>
              <td>{isSrt ? "SRT Translation" : "Video"}</td>
              <td className="file-cell" title={fileTitle}>
                {fileIdentity.split("/").pop()}
              </td>
              <td className="date-cell" title={fmtDateTime(job.created_at)}>
                {fmtDateTime(job.created_at)}
              </td>
              <td>
                <JobStatusBadge status={job.status} />
              </td>
              <td>
                {job.stage}
                {job.detected_language && ` (${job.detected_language})`}
              </td>
              <td>
                <ProgressBar progress={job.progress} status={job.status} elapsedSeconds={job.elapsed_seconds} />
              </td>
              <td>{fmtElapsed(job.elapsed_seconds)}</td>
              <td>{qcSummary(job)}</td>
              <td className="actions">
                {ACTIVE.has(job.status) && (
                  <button
                    className="text-button"
                    onClick={() => handle(cancelJob.mutateAsync(job.id), "Cancel")}
                  >
                    Cancel
                  </button>
                )}
                {RETRYABLE.has(job.status) && (
                  <button
                    className="text-button"
                    onClick={() => handle(retryJob.mutateAsync({ id: job.id }), "Retry")}
                  >
                    Retry
                  </button>
                )}
                {DELETABLE.has(job.status) && (
                  <button
                    className={`text-button${job.status === "completed" ? " danger" : ""}`}
                    onClick={() => handle(deleteJob.mutateAsync(job.id), "Delete")}
                  >
                    Delete
                  </button>
                )}
                <Link className="text-button" to={`/jobs/${job.id}`}>
                  Log
                </Link>
              </td>
            </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
