import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { HardwareProfile, StorageInfo } from "../types";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card" style={{ padding: 16, flex: 1, minWidth: 160 }}>
      <div className="muted" style={{ fontSize: 11 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 800, marginTop: 4 }}>{value}</div>
    </div>
  );
}

export default function HardwarePage() {
  const [hardware, setHardware] = useState<HardwareProfile | null>(null);
  const [storage, setStorage] = useState<StorageInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getHardware().then(setHardware).catch((e) => setError(e.message));
    api.getStorage().then(setStorage).catch(() => {});
  }, []);

  if (error) return <div className="content"><div className="error-banner">{error}</div></div>;
  if (!hardware) return <div className="content">Loading…</div>;

  return (
    <div className="content">
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        <Stat label="Acceleration" value={hardware.vendor === "cpu" ? "CPU only" : hardware.vendor.toUpperCase()} />
        <Stat label="CPU Cores" value={String(hardware.cpu_cores)} />
        <Stat label="System RAM" value={`${Math.round(hardware.total_ram_mb / 1024)} GB`} />
        {storage && (
          <Stat label="Disk Free" value={`${(storage.free_bytes / 1e9).toFixed(0)} GB / ${(storage.total_bytes / 1e9).toFixed(0)} GB`} />
        )}
      </div>

      {hardware.fallback_reason && (
        <div className="error-banner" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>
          Running in fallback mode: {hardware.fallback_reason}
        </div>
      )}

      <div className="card" style={{ padding: 16 }}>
        <div style={{ fontWeight: 800, fontSize: 13.5, marginBottom: 12 }}>GPUs ({hardware.gpu_count})</div>
        {hardware.gpu_count === 0 && <div className="muted" style={{ fontSize: 12.5 }}>No GPU detected — pipeline stages will run on CPU, which is significantly slower for ASR and translation.</div>}
        <table>
          <thead><tr><th>Index</th><th>Name</th><th>VRAM</th></tr></thead>
          <tbody>
            {hardware.gpus.map((g) => (
              <tr key={g.index}><td>{g.index}</td><td>{g.name}</td><td>{g.total_vram_mb} MB</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
