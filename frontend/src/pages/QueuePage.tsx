import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import StatusPill from "../components/StatusPill";
import type { Job } from "../types";

export default function QueuePage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setJobs(await api.listJobs());
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 4000);
    return () => clearInterval(interval);
  }, []);

  async function act(fn: () => Promise<Job>) {
    try {
      await fn();
      refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="content">
      {error && <div className="error-banner">{error}</div>}
      <div className="card" style={{ padding: 8 }}>
        <table>
          <thead>
            <tr>
              <th>File</th><th>Job</th><th>Status</th><th>Priority</th><th>Retries</th><th>Progress</th><th style={{ width: 260 }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td>
                  <Link to={`/jobs/${job.id}`} style={{ textDecoration: "none", color: "inherit", fontWeight: 700 }}>
                    {job.display_filename ?? <span className="mono muted">{job.id.slice(0, 8)}</span>}
                  </Link>
                </td>
                <td><Link to={`/jobs/${job.id}`} className="mono muted">{job.id.slice(0, 8)}</Link></td>
                <td><StatusPill status={job.status} /></td>
                <td>{job.priority}</td>
                <td>{job.retry_count} / {job.max_retries}</td>
                <td style={{ width: 160 }}>
                  {job.status === "running" ? (
                    <div className="bar-track"><div className="bar-fill" style={{ width: `${job.progress_pct}%` }} /></div>
                  ) : (
                    <span className="muted">{job.current_stage ?? "—"}</span>
                  )}
                </td>
                <td>
                  <div style={{ display: "flex", gap: 6 }}>
                    {job.status === "queued" && (
                      <button className="btn-ghost" onClick={() => act(() => api.pauseJob(job.id))}>Pause</button>
                    )}
                    {job.status === "paused" && (
                      <button className="btn-ghost" onClick={() => act(() => api.resumeJob(job.id))}>Resume</button>
                    )}
                    {job.status === "needs_decision" && (
                      <>
                        <button className="btn-ghost" onClick={() => act(() => api.decideExistingOutput(job.id, "replace"))}>Replace</button>
                        <button className="btn-ghost" onClick={() => act(() => api.decideExistingOutput(job.id, "keep"))}>Keep existing</button>
                      </>
                    )}
                    {job.status === "failed" && (
                      <button className="btn-ghost" onClick={() => act(() => api.retryJob(job.id))}>Retry</button>
                    )}
                    {["queued", "running", "paused", "needs_decision"].includes(job.status) && (
                      <button className="btn-ghost btn-danger" onClick={() => act(() => api.cancelJob(job.id))}>Cancel</button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {jobs.length === 0 && (
              <tr><td colSpan={7} className="muted" style={{ textAlign: "center", padding: 24 }}>No jobs yet — start one from New Job.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
