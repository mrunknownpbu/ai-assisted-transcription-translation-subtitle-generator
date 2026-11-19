import { NavLink, Route, Routes } from "react-router-dom";

import { useEventStream } from "./api/useEventStream";
import { useQueueCounts } from "./api/hooks";
import { ToastProvider } from "./components/Toast";
import { JobDetailPage } from "./pages/JobDetailPage";
import { JobsPage } from "./pages/JobsPage";
import { LibraryPage } from "./pages/LibraryPage";
import { RetimePage } from "./pages/RetimePage";
import { SeriesDetailPage } from "./pages/SeriesDetailPage";
import { SeriesListPage } from "./pages/SeriesListPage";
import { TranslateSrtPage } from "./pages/TranslateSrtPage";

function QueueSummary() {
  const { data } = useQueueCounts();
  if (!data) return <div className="queue-summary status">Loading queue&hellip;</div>;
  const stats: Array<[string, number, string]> = [
    ["queued", data.QUEUED, "queued"],
    ["running", data.RUNNING, "running"],
    ["completed", data.COMPLETED, "completed"],
    ["failed", data.FAILED, "failed"],
  ];
  return (
    <div className="queue-summary status" aria-label="Queue summary">
      {stats.map(([label, n, tone]) => (
        <span key={label} className={`queue-stat queue-stat-${tone}`}>
          <strong>{n}</strong> {label}
        </span>
      ))}
    </div>
  );
}

export function App() {
  useEventStream();

  return (
    <ToastProvider>
      <div className="app-shell">
        <aside className="sidebar">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true">S</span>
            <h1>Subtitle AI</h1>
          </div>
          <nav className="nav">
          <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>
            Library
          </NavLink>
          <NavLink to="/series" className={({ isActive }) => (isActive ? "active" : "")}>
            Series
          </NavLink>
          <NavLink to="/translate" className={({ isActive }) => (isActive ? "active" : "")}>
            Translate Subtitle
          </NavLink>
          <NavLink to="/retime" className={({ isActive }) => (isActive ? "active" : "")}>
            Re-time Subtitle
          </NavLink>
          <NavLink to="/jobs" className={({ isActive }) => (isActive ? "active" : "")}>
            Jobs
          </NavLink>
          </nav>
          <QueueSummary />
        </aside>
      <main>
        <Routes>
          <Route path="/" element={<LibraryPage />} />
          <Route path="/series" element={<SeriesListPage />} />
          <Route path="/series/:tvdbId" element={<SeriesDetailPage />} />
          <Route path="/translate" element={<TranslateSrtPage />} />
          <Route path="/retime" element={<RetimePage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/jobs/:id" element={<JobDetailPage />} />
        </Routes>
      </main>
      </div>
    </ToastProvider>
  );
}
