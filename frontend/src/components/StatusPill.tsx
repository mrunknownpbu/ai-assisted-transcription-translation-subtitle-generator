const STATUS_COLORS: Record<string, string> = {
  queued: "amber",
  running: "blue",
  paused: "amber",
  needs_decision: "purple",
  canceled: "mute",
  failed: "red",
  done: "green",
};

export default function StatusPill({ status }: { status: string }) {
  const color = STATUS_COLORS[status] ?? "mute";
  return (
    <span className="pill" style={{ background: `var(--${color}-soft, var(--card-soft))`, color: `var(--${color}, var(--text-mute))` }}>
      {status.replace("_", " ")}
    </span>
  );
}
