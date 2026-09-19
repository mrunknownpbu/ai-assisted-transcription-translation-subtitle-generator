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
