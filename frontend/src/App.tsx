import { NavLink, Route, Routes } from "react-router-dom";

import { useEventStream } from "./api/useEventStream";
import { useQueueCounts } from "./api/hooks";
import { ToastProvider } from "./components/Toast";
import { JobDetailPage } from "./pages/JobDetailPage";
import { JobsPage } from "./pages/JobsPage";
import { LibraryPage } from "./pages/LibraryPage";
import { SeriesDetailPage } from "./pages/SeriesDetailPage";
import { SeriesListPage } from "./pages/SeriesListPage";
import { TranslateSrtPage } from "./pages/TranslateSrtPage";

function QueueSummary() {
  const { data } = useQueueCounts();
  if (!data) return <span className="status">Loading queue&hellip;</span>;
  return (
    <span className="status">
      {data.QUEUED} queued &middot; {data.RUNNING} running &middot; {data.COMPLETED} completed &middot;{" "}
      {data.FAILED} failed
    </span>
  );
}

export function App() {
  useEventStream();

  return (
    <ToastProvider>
      <header className="topbar">
        <div>
          <h1>Subtitle AI</h1>
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
            <NavLink to="/jobs" className={({ isActive }) => (isActive ? "active" : "")}>
              Jobs
            </NavLink>
          </nav>
        </div>
        <QueueSummary />
      </header>
      <main>
        <Routes>
          <Route path="/" element={<LibraryPage />} />
          <Route path="/series" element={<SeriesListPage />} />
          <Route path="/series/:tvdbId" element={<SeriesDetailPage />} />
          <Route path="/translate" element={<TranslateSrtPage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/jobs/:id" element={<JobDetailPage />} />
        </Routes>
      </main>
    </ToastProvider>
  );
}
