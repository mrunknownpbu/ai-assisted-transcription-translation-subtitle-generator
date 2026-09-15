import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { MediaFile } from "../types";

function formatBytes(bytes: number): string {
  const gb = bytes / 1e9;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${(bytes / 1e6).toFixed(0)} MB`;
}

export default function MediaLibraryPage() {
  const [media, setMedia] = useState<MediaFile[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listMedia().then(setMedia).catch((e) => setError(e.message));
  }, []);

  return (
    <div className="content">
      {error && <div className="error-banner">{error}</div>}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ fontSize: 15, fontWeight: 800 }}>Media Library</div>
        <Link to="/new" className="btn btn-primary" style={{ textDecoration: "none" }}>+ Register From Library</Link>
      </div>
      <div className="card" style={{ padding: 8 }}>
        <table>
          <thead>
            <tr><th>File</th><th>Format</th><th>Size</th><th>Audio Streams</th><th>TVDB</th><th>Inspected</th></tr>
          </thead>
          <tbody>
            {media.map((m) => (
              <tr key={m.id}>
                <td>{m.filename}</td>
                <td className="muted">{m.container_format}</td>
                <td className="muted">{formatBytes(m.size_bytes)}</td>
                <td className="muted">{m.audio_streams.length}</td>
                <td>
                  {m.tvdb_id
                    ? <span className="pill" style={{ background: "var(--green-soft)", color: "var(--green)" }}>#{m.tvdb_id}</span>
                    : <span className="muted">—</span>}
                </td>
                <td className="muted">{new Date(m.inspected_at).toLocaleString()}</td>
              </tr>
            ))}
            {media.length === 0 && (
              <tr><td colSpan={6} className="muted" style={{ textAlign: "center", padding: 24 }}>No media registered yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
