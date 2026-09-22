import { fmtEta } from "../format";
import type { JobStatus } from "../api/types";

interface Props {
  progress: number;
  status: JobStatus;
  elapsedSeconds: number;
}

// IMPROVEMENT_PLAN.md 4.3: job.progress now moves through real
// milestones as a job runs (worker.py's _stage_progress_for_event()),
// not just 0 then 100 -- this renders that as an actual bar instead of
// bare percentage text, plus a best-effort ETA (see fmtEta()'s own
// docstring for why it's deliberately conservative about when to show
// one at all).
export function ProgressBar({ progress, status, elapsedSeconds }: Props) {
  const pct = Math.max(0, Math.min(100, progress));
  const eta = status === "running" ? fmtEta(elapsedSeconds, progress) : null;
  return (
    <div className="progress-cell">
      <div
        className="progress-bar"
        role="progressbar"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className={`progress-bar-fill status-${status}`} style={{ width: `${pct}%` }} />
      </div>
      <div className="progress-text">
        {Math.round(pct)}%{eta ? ` · ${eta}` : ""}
      </div>
    </div>
  );
}
