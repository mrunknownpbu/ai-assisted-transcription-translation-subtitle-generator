import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import StatusPill from "../components/StatusPill";
import type { Job } from "../types";
import JobWizard from "./NewJobPage";

function Icon({ d, size = 20 }: { d: JSX.Element; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      {d}
    </svg>
  );
}

const FEATURES = [
  { icon: <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Zm-7 9a7 7 0 0 0 14 0M12 18v4" />, color: "blue", title: "Audio-First", sub: "Uses actual audio, not existing subtitles" },
  { icon: <path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM3 12h18M12 3a14 14 0 0 1 0 18 14 14 0 0 1 0-18Z" />, color: "green", title: "Any Language", sub: "Translate to any target language" },
  { icon: <path d="M13 2 3 14h7l-1 8 11-14h-7l0-6Z" />, color: "purple", title: "Multi-Engine", sub: "Whisper + fallback, CPU/GPU optimized" },
];

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min${mins === 1 ? "" : "s"} ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  return `${Math.round(hours / 24)} day${Math.round(hours / 24) === 1 ? "" : "s"} ago`;
}

function displayName(job: Job): string {
  return job.display_filename ?? `${job.job_type === "direct_translation" ? "Subtitle" : "Job"} ${job.id.slice(0, 8)}`;
}

function ProcessingQueue({ jobs }: { jobs: Job[] }) {
  const active = jobs.filter((j) => j.status === "queued" || j.status === "running" || j.status === "paused");
  return (
    <div className="card panel">
      <div className="panel-head">
        <span className="title">Processing Queue ({active.length})</span>
        <Link to="/new" className="btn btn-primary" style={{ padding: "6px 12px", fontSize: 12, textDecoration: "none" }}>+ New Job</Link>
      </div>
      {active.length === 0 && <div className="muted" style={{ fontSize: 12 }}>No active jobs — start one above.</div>}
      {active.map((job) => (
        <Link key={job.id} to={`/jobs/${job.id}`} className="queue-item" style={{ textDecoration: "none", color: "inherit" }}>
          <div className="queue-thumb"><Icon d={<path d="M8 5v14l11-7Z" />} size={16} /></div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="name">{displayName(job)}</div>
            {job.status === "running" ? (
              <div className="bar-track" style={{ marginTop: 5 }}><div className="bar-fill" style={{ width: `${job.progress_pct}%` }} /></div>
            ) : (
              <div className="meta">{job.status === "queued" ? "Waiting…" : job.status}</div>
            )}
          </div>
          {job.status === "running" && <span className="muted" style={{ fontSize: 10.5, whiteSpace: "nowrap" }}>{job.current_stage}</span>}
        </Link>
      ))}
    </div>
  );
}

function RecentJobs({ jobs }: { jobs: Job[] }) {
  const recent = jobs
    .filter((j) => ["done", "failed", "canceled"].includes(j.status))
    .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
    .slice(0, 5);
  return (
    <div className="card panel">
      <div className="panel-head">
        <span className="title">Recent Jobs</span>
        <Link to="/history" className="muted" style={{ fontSize: 11.5, textDecoration: "none" }}>View All</Link>
      </div>
      {recent.length === 0 && <div className="muted" style={{ fontSize: 12 }}>Nothing completed yet.</div>}
      {recent.map((job) => (
        <Link key={job.id} to={`/jobs/${job.id}`} className="recent-item" style={{ textDecoration: "none", color: "inherit" }}>
          <div className="thumb" />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="name">{displayName(job)}</div>
            <div className="meta"><StatusPill status={job.status} /> · {timeAgo(job.updated_at)}</div>
          </div>
        </Link>
      ))}
    </div>
  );
}

export default function DashboardPage({ wizardOnly = false }: { wizardOnly?: boolean }) {
  const [jobs, setJobs] = useState<Job[]>([]);

  useEffect(() => {
    function refresh() {
      api.listJobs().then(setJobs).catch(() => {});
    }
    refresh();
    const interval = setInterval(refresh, 4000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="content">
      {!wizardOnly && (
        <div className="hero">
          <h1>Generate Subtitles with AI</h1>
          <p className="hero-sub">Accurate transcription. Natural translation. Precise timing.</p>
          <div className="feature-row">
            {FEATURES.map((f) => (
              <div key={f.title} className="feature-badge">
                <div className="icon-circle" style={{ background: `var(--${f.color}-soft)`, color: `var(--${f.color})` }}>
                  <Icon d={f.icon} size={17} />
                </div>
                <div>
                  <div className="title">{f.title}</div>
                  <div className="sub">{f.sub}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {wizardOnly ? (
        <JobWizard />
      ) : (
        <div className="dashboard-grid">
          <JobWizard />
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <ProcessingQueue jobs={jobs} />
            <RecentJobs jobs={jobs} />
          </div>
        </div>
      )}
    </div>
  );
}
