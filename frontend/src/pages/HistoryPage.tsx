import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import StatusPill from "../components/StatusPill";
import type { Job } from "../types";

/** Shared "list of jobs" table. `logsMode` widens scope to every job (not just finished
 * ones) and links each row straight at its per-stage log/QC view on the job detail page --
 * there is no separate system-wide log stream, so this is the honest way to expose "Logs"
 * as a nav item without fabricating one. */
export default function HistoryPage({ logsMode = false }: { logsMode?: boolean }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listJobs().then(setJobs).catch((e) => setError(e.message));
  }, []);

  const rows = (logsMode ? jobs : jobs.filter((j) => ["done", "failed", "canceled"].includes(j.status)))
    .slice()
    .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());

  return (
    <div className="content">
      {error && <div className="error-banner">{error}</div>}
      {!logsMode && <div style={{ fontSize: 15, fontWeight: 800 }}>Job History</div>}
      {logsMode && (
        <div className="muted" style={{ fontSize: 12.5 }}>
          Per-job stage logs, suppression decisions, and QC reports — open a job to view its full pipeline log.
        </div>
      )}
      <div className="card" style={{ padding: 8 }}>
        <table>
          <thead>
            <tr><th>File</th><th>Type</th><th>Status</th><th>Targets</th><th>Updated</th><th /></tr>
          </thead>
          <tbody>
            {rows.map((job) => (
              <tr key={job.id}>
                <td>{job.display_filename ?? <span className="mono muted">{job.id.slice(0, 8)}</span>}</td>
                <td className="muted">{job.job_type === "direct_translation" ? "Subtitle translation" : "Audio pipeline"}</td>
                <td><StatusPill status={job.status} /></td>
                <td className="muted">{job.target_languages.join(", ")}</td>
                <td className="muted">{new Date(job.updated_at).toLocaleString()}</td>
                <td><Link to={`/jobs/${job.id}`} className="btn-ghost" style={{ textDecoration: "none" }}>{logsMode ? "View Logs" : "Open"}</Link></td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={6} className="muted" style={{ textAlign: "center", padding: 24 }}>
                {logsMode ? "No jobs yet." : "No completed jobs yet."}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
