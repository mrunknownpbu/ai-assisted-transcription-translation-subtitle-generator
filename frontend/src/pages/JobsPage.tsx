import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useJobs } from "../api/hooks";
import { JobTable } from "../components/JobTable";

const TABS = ["ALL", "QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "SKIPPED"];

// Everything on screen that a job change affects: the list, the header's
// "N queued / N running" line (a separate query, in App), and the series counts.
const REFRESH_KEYS = ["jobs", "queue", "series"];

export function JobsPage() {
  const [tab, setTab] = useState("ALL");
  const { data, isLoading, dataUpdatedAt } = useJobs(tab);
  const queryClient = useQueryClient();
  // Local state, not useIsFetching(): live job events refetch every second or
  // so while something is running, which would keep the button flickering to
  // "Refreshing…" and disabled without the user having clicked anything.
  const [refreshing, setRefreshing] = useState(false);

  const refresh = async () => {
    setRefreshing(true);
    try {
      await Promise.all(
        REFRESH_KEYS.map((key) => queryClient.refetchQueries({ queryKey: [key], type: "active" })),
      );
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <section className="panel jobs-panel">
      <div className="panel-head">
        <h2>Job history</h2>
        <div className="refresh-control">
          {dataUpdatedAt > 0 && (
            <span className="refresh-updated">
              Updated {new Date(dataUpdatedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
            </span>
          )}
          <button className="text-button" onClick={refresh} disabled={refreshing}>
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t}
            className={`tab${t === tab ? " active" : ""}`}
            role="tab"
            aria-selected={t === tab}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>
      {isLoading ? <div>Loading&hellip;</div> : <JobTable jobs={data?.jobs ?? []} />}
    </section>
  );
}
