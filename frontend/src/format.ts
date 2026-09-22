// Ported from the old static/app.js's fmtDuration/fmtElapsed/fmtBytes --
// same display conventions, just typed.

export function fmtDuration(seconds: number | null | undefined): string {
  if (!seconds) return "—";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h ? `${h}h ${m}m ${sec}s` : `${m}m ${sec}s`;
}

export function fmtElapsed(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const s = Math.round(seconds);
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

export function fmtBytes(bytes: number | null | undefined): string {
  if (!bytes) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = bytes;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export function fmtPercent(confidence: number | null | undefined): string {
  if (confidence == null) return "—";
  return `${Math.round(confidence * 100)}%`;
}

// IMPROVEMENT_PLAN.md 4.3: worker.py now writes a real 0-100 `progress`
// as a job actually moves through its stages (previously only 0 or 100
// -- see worker.py's _stage_progress_for_event() docstring), so a linear
// extrapolation from elapsed time is meaningful here in a way it wasn't
// before. Deliberately conservative: returns null (render "—", not a
// number) below 2% progress, where elapsed/progress blows up into a wild
// extrapolation from almost no signal, and once at/above 100% (nothing
// left to estimate).
export function fmtEta(elapsedSeconds: number | null | undefined, progress: number | null | undefined): string | null {
  if (elapsedSeconds == null || progress == null) return null;
  if (progress < 2 || progress >= 100) return null;
  const totalEstimate = elapsedSeconds / (progress / 100);
  const remaining = totalEstimate - elapsedSeconds;
  if (!Number.isFinite(remaining) || remaining <= 0) return null;
  return `~${fmtDuration(remaining)} left`;
}

// job.created_at etc. are Unix seconds (Python time.time()), matching
// every other timestamp field this API returns -- *1000 for JS's
// millisecond-based Date. Uses the viewer's own locale/timezone rather
// than a fixed format, same convention as the browser's other
// locale-formatted UI text.
export function fmtDateTime(seconds: number | null | undefined): string {
  if (!seconds) return "—";
  return new Date(seconds * 1000).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
