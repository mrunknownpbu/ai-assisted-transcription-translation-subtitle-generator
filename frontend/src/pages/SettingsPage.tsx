import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { AppSettings } from "../types";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "10px 0", borderTop: "1px solid var(--border-soft)" }}>
      <span className="muted" style={{ fontSize: 13 }}>{label}</span>
      <span style={{ fontSize: 13, fontWeight: 700 }}>{value}</span>
    </div>
  );
}

function Badge({ ok, onText, offText }: { ok: boolean; onText: string; offText: string }) {
  return ok
    ? <span className="pill" style={{ background: "var(--green-soft)", color: "var(--green)" }}>{onText}</span>
    : <span className="pill" style={{ background: "var(--card-soft)", color: "var(--text-mute)" }}>{offText}</span>;
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getSettings().then(setSettings).catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="content"><div className="error-banner">{error}</div></div>;
  if (!settings) return <div className="content">Loading…</div>;

  return (
    <div className="content">
      <div style={{ fontSize: 15, fontWeight: 800 }}>Settings</div>
      <div className="muted" style={{ fontSize: 12.5, marginTop: -8 }}>
        Read-only view of this deployment's configuration. Change these via environment variables in the compose
        file, not here — there's no in-app settings mutation yet.
      </div>

      <div className="card" style={{ padding: 18 }}>
        <div style={{ fontWeight: 800, fontSize: 13, marginBottom: 4 }}>Models</div>
        <Row label="ASR model (Whisper)" value={<span className="mono">{settings.whisper_model_size}</span>} />
        <Row label="Translation model (NLLB)" value={<span className="mono">{settings.nllb_model_name}</span>} />
        <Row label="Max concurrent GPU jobs (per GPU)" value={settings.max_concurrent_gpu_jobs} />
      </div>

      <div className="card" style={{ padding: 18 }}>
        <div style={{ fontWeight: 800, fontSize: 13, marginBottom: 4 }}>Integrations</div>
        <Row
          label="Media library (register/browse by path)"
          value={<Badge ok={settings.library_configured} onText="Configured" offText="Not configured" />}
        />
        <Row
          label="TVDB (auto glossary from cast)"
          value={<Badge ok={settings.tvdb_configured} onText="Configured" offText="Not configured" />}
        />
      </div>
    </div>
  );
}
