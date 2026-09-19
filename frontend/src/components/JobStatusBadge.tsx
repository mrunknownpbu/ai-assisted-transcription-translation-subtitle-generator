import type { JobStatus } from "../api/types";

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return <span className={`badge badge-${status}`}>{status}</span>;
}
