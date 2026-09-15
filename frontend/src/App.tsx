import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api/client";
import DashboardPage from "./pages/DashboardPage";
import HardwarePage from "./pages/HardwarePage";
import HelpPage from "./pages/HelpPage";
import HistoryPage from "./pages/HistoryPage";
import JobDetailPage from "./pages/JobDetailPage";
import MediaLibraryPage from "./pages/MediaLibraryPage";
import QueuePage from "./pages/QueuePage";
import SettingsPage from "./pages/SettingsPage";
import TranslateSubtitlesPage from "./pages/TranslateSubtitlesPage";
import type { HardwareProfile, Job, StorageInfo } from "./types";

const ICONS = {
  dashboard: <path d="M4 13h7V4H4v9Zm9 7h7V4h-7v16ZM4 20h7v-5H4v5Z" />,
  newJob: <path d="M12 5v14M5 12h14" />,
  queue: <path d="M4 6h16M4 12h16M4 18h9" />,
  history: <path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5M12 8v5l3 2" />,
  library: <path d="M4 6.5A1.5 1.5 0 0 1 5.5 5h4l2 2.5h7A1.5 1.5 0 0 1 20 9v8.5A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5v-11Z" />,
  settings: <path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Zm8-3.5c0 .6-.05 1.2-.14 1.77l2.1 1.63-2 3.46-2.48-1a7.7 7.7 0 0 1-1.53.89l-.38 2.65H9.43l-.38-2.65a7.7 7.7 0 0 1-1.53-.89l-2.48 1-2-3.46 2.1-1.63A8.3 8.3 0 0 1 5 12c0-.6.05-1.2.14-1.77l-2.1-1.63 2-3.46 2.48 1a7.7 7.7 0 0 1 1.53-.89L9.43 2.6h5.14l.38 2.65c.55.24 1.06.54 1.53.89l2.48-1 2 3.46-2.1 1.63c.09.57.14 1.17.14 1.77Z" />,
  hardware: <path d="M4 5h16v10H4V5Zm3 14h10M9 15v4M15 15v4" />,
  logs: <path d="M6 4h9l5 5v11H6V4Zm9 0v5h5M9 12h6M9 16h6" />,
  help: <path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-5.5v-.25c0-.9.6-1.35 1.2-1.8.65-.48 1.3-.97 1.3-1.95a2.5 2.5 0 0 0-5 0M12 17.2v.1" />,
  search: <path d="M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm10 2-4.35-4.35" />,
  bell: <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9ZM13.7 21a2 2 0 0 1-3.4 0" />,
  moon: <path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z" />,
  sun: <path d="M12 3v2m0 14v2m9-9h-2M5 12H3m14.6-6.6-1.4 1.4M6.8 17.2l-1.4 1.4m0-13.2 1.4 1.4M17.2 17.2l1.4 1.4M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z" />,
};

function Icon({ d, size = 15 }: { d: JSX.Element; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      {d}
    </svg>
  );
}

function StorageWidget() {
  const [storage, setStorage] = useState<StorageInfo | null>(null);
  useEffect(() => { api.getStorage().then(setStorage).catch(() => setStorage(null)); }, []);
  if (!storage) return null;
  const usedGb = storage.used_bytes / 1e9;
  const totalGb = storage.total_bytes / 1e9;
  const pct = Math.min(100, (storage.used_bytes / storage.total_bytes) * 100);
  const fmt = (gb: number) => (gb >= 1000 ? `${(gb / 1000).toFixed(1)} TB` : `${gb.toFixed(0)} GB`);
  return (
    <div className="storage-widget">
      <div className="label">
        <span className="muted" style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Icon d={ICONS.library} size={12} /> Storage Usage
        </span>
        <span style={{ fontWeight: 700 }}>{Math.round(pct)}%</span>
      </div>
      <div className="bar-track"><div className="bar-fill" style={{ width: `${pct}%`, background: pct > 90 ? "var(--red)" : "var(--blue)" }} /></div>
      <div className="muted" style={{ fontSize: 10.5, marginTop: 6 }}>{fmt(usedGb)} / {fmt(totalGb)}</div>
    </div>
  );
}

function Sidebar({ queuedCount }: { queuedCount: number }) {
  return (
    <div className="sidebar">
      <div className="brand">
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
          <rect x="1.5" y="9" width="3.2" height="7" rx="1.6" fill="#4f8bff" />
          <rect x="7" y="4.5" width="3.2" height="16" rx="1.6" fill="#9b6bff" />
          <rect x="12.5" y="1.5" width="3.2" height="22" rx="1.6" fill="#4f8bff" />
          <rect x="18" y="6.5" width="3.2" height="12" rx="1.6" fill="#2fd18c" />
        </svg>
        <div>
          <div className="brand-name">SubtitleAI</div>
          <div className="brand-sub">Transcribe · Translate · Subtitle</div>
        </div>
      </div>
      <nav className="nav">
        <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}><Icon d={ICONS.dashboard} /> Dashboard</NavLink>
        <NavLink to="/new" className={({ isActive }) => (isActive ? "active" : "")}><Icon d={ICONS.newJob} /> New Job</NavLink>
        <NavLink to="/translate" className={({ isActive }) => (isActive ? "active" : "")}><Icon d={ICONS.newJob} /> Translate Subtitles</NavLink>
        <NavLink to="/queue" className={({ isActive }) => (isActive ? "active" : "")}>
          <Icon d={ICONS.queue} /> Job Queue
          {queuedCount > 0 && <span className="nav-badge">{queuedCount}</span>}
        </NavLink>
        <NavLink to="/history" className={({ isActive }) => (isActive ? "active" : "")}><Icon d={ICONS.history} /> History</NavLink>
        <NavLink to="/library" className={({ isActive }) => (isActive ? "active" : "")}><Icon d={ICONS.library} /> Media Library</NavLink>
        <NavLink to="/settings" className={({ isActive }) => (isActive ? "active" : "")}><Icon d={ICONS.settings} /> Settings</NavLink>

        <div className="nav-group-label">System</div>
        <NavLink to="/hardware" className={({ isActive }) => (isActive ? "active" : "")}><Icon d={ICONS.hardware} /> Hardware</NavLink>
        <NavLink to="/logs" className={({ isActive }) => (isActive ? "active" : "")}><Icon d={ICONS.logs} /> Logs</NavLink>
        <NavLink to="/help" className={({ isActive }) => (isActive ? "active" : "")}><Icon d={ICONS.help} /> Help</NavLink>
      </nav>
      <StorageWidget />
    </div>
  );
}

function TopBar({ hardware }: { hardware: HardwareProfile | null }) {
  const [theme, setTheme] = useState<"dark" | "light">(() => (localStorage.getItem("subtitleai-theme") as "dark" | "light") || "dark");
  const [showNotifications, setShowNotifications] = useState(false);
  const [recentEvents, setRecentEvents] = useState<Job[]>([]);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("subtitleai-theme", theme);
  }, [theme]);

  useEffect(() => {
    api.listJobs().then((jobs) => {
      setRecentEvents(jobs.filter((j) => j.status === "done" || j.status === "failed").slice(0, 5));
    }).catch(() => {});
  }, []);

  const gpuLabel = hardware
    ? hardware.vendor === "cpu" ? "CPU-only" : `${hardware.gpus[0]?.name ?? hardware.vendor}`
    : "Detecting…";

  return (
    <div className="topbar">
      <div className="topbar-search">
        <Icon d={ICONS.search} size={14} />
        <span>Search jobs, media, or files…</span>
        <kbd>Ctrl + K</kbd>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div className="pill" style={{ background: "var(--green-soft)", color: "var(--green)" }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--green)" }} />
          All Systems Operational
        </div>
        <span className="muted" style={{ fontSize: 11 }}>v0.1.0</span>
        <button className="icon-btn" title={gpuLabel} onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
          <Icon d={theme === "dark" ? ICONS.moon : ICONS.sun} />
        </button>
        <div style={{ position: "relative" }}>
          <button className="icon-btn" onClick={() => setShowNotifications((v) => !v)}>
            <Icon d={ICONS.bell} />
            {recentEvents.length > 0 && <span className="dot" />}
          </button>
          {showNotifications && (
            <div className="card" style={{ position: "absolute", right: 0, top: 42, width: 280, padding: 10, zIndex: 20 }}>
              <div style={{ fontWeight: 800, fontSize: 12, marginBottom: 8 }}>Recent activity</div>
              {recentEvents.length === 0 && <div className="muted" style={{ fontSize: 12 }}>Nothing yet.</div>}
              {recentEvents.map((j) => (
                <div key={j.id} style={{ fontSize: 11.5, padding: "6px 0", borderTop: "1px solid var(--border-soft)" }}>
                  {j.display_filename ?? j.id.slice(0, 8)} — <span className={j.status === "done" ? "" : "muted"}>{j.status}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="user-chip">
          <div className="user-avatar">BA</div>
          <span style={{ fontSize: 13, fontWeight: 700 }}>Badrul</span>
        </div>
      </div>
    </div>
  );
}

function Footer() {
  return (
    <div className="app-footer">
      <span>SubtitleAI v0.1.0 · Open Source</span>
      <div className="links">
        <span>Self-hosted</span>
        <span>Audio is the source of truth. Always.</span>
        <span>Built for media lovers ❤️</span>
      </div>
    </div>
  );
}

export default function App() {
  const [hardware, setHardware] = useState<HardwareProfile | null>(null);
  const [queuedCount, setQueuedCount] = useState(0);

  useEffect(() => {
    api.getHardware().then(setHardware).catch(() => setHardware(null));
  }, []);

  useEffect(() => {
    function refreshQueueCount() {
      api.listJobs().then((jobs) => {
        setQueuedCount(jobs.filter((j) => j.status === "queued" || j.status === "running").length);
      }).catch(() => {});
    }
    refreshQueueCount();
    const interval = setInterval(refreshQueueCount, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="app-shell">
      <Sidebar queuedCount={queuedCount} />
      <div className="main">
        <TopBar hardware={hardware} />
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/new" element={<DashboardPage wizardOnly />} />
          <Route path="/translate" element={<TranslateSubtitlesPage />} />
          <Route path="/queue" element={<QueuePage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/library" element={<MediaLibraryPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/hardware" element={<HardwarePage />} />
          <Route path="/logs" element={<HistoryPage logsMode />} />
          <Route path="/help" element={<HelpPage />} />
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
        </Routes>
        <Footer />
      </div>
    </div>
  );
}
