import { useState } from "react";

import { useJobs } from "../api/hooks";
import { JobTable } from "../components/JobTable";

const TABS = ["ALL", "QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "SKIPPED"];

export function JobsPage() {
  const [tab, setTab] = useState("ALL");
  const { data, isLoading, refetch } = useJobs(tab);

  return (
    <section className="panel jobs-panel">
      <div className="panel-head">
        <h2>Job history</h2>
        <button className="text-button" onClick={() => refetch()}>
          Refresh
        </button>
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
