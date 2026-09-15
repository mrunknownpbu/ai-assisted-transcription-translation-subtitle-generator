import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";

export default function TranslateSubtitlesPage() {
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [targetLanguages, setTargetLanguages] = useState<string[]>([]);
  const [newTargetLang, setNewTargetLang] = useState("");
  const [outputFormats, setOutputFormats] = useState<string[]>(["srt", "vtt", "webvtt"]);
  const [glossaryText, setGlossaryText] = useState("");
  const [tvdbId, setTvdbId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function addTargetLanguage() {
    const value = newTargetLang.trim();
    if (value && !targetLanguages.includes(value)) setTargetLanguages([...targetLanguages, value]);
    setNewTargetLang("");
  }

  function toggleFormat(fmt: string) {
    setOutputFormats((prev) => (prev.includes(fmt) ? prev.filter((f) => f !== fmt) : [...prev, fmt]));
  }

  async function submit() {
    if (!file || targetLanguages.length === 0) return;
    setSubmitting(true);
    setError(null);
    try {
      let glossaryEntities;
      if (glossaryText.trim()) {
        try {
          glossaryEntities = JSON.parse(glossaryText);
        } catch {
          throw new Error('Glossary must be valid JSON, e.g. [{"canonical": "Eda", "aliases": ["Edacim"]}]');
        }
      }
      const job = await api.createDirectTranslationJob({
        file, target_languages: targetLanguages, output_formats: outputFormats, glossary_entities: glossaryEntities,
        tvdb_id: tvdbId.trim() ? Number(tvdbId.trim()) : null,
      });
      navigate(`/jobs/${job.id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="content">
      <div className="card" style={{ padding: 22 }}>
        <div style={{ fontSize: 15, fontWeight: 800, marginBottom: 4 }}>Translate Existing Subtitles</div>
        <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
          Upload a .srt file you already have and translate its text directly — no audio, no transcription.
        </div>
        <div className="error-banner" style={{ background: "var(--amber-soft)", color: "var(--amber)", marginBottom: 16 }}>
          This output is translated from the subtitle file you provide, not verified against any audio.
        </div>
        {error && <div className="error-banner" style={{ marginBottom: 16 }}>{error}</div>}

        <div
          onClick={() => fileInputRef.current?.click()}
          style={{
            border: "1.5px dashed var(--border)", borderRadius: 12, padding: "32px 0", textAlign: "center",
            cursor: "pointer", background: "var(--card-soft)", marginBottom: 20,
          }}
        >
          {file ? file.name : "Click to select a .srt file"}
        </div>
        <input
          ref={fileInputRef} type="file" accept=".srt" style={{ display: "none" }}
          onChange={(e) => e.target.files?.[0] && setFile(e.target.files[0])}
        />

        <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>TARGET LANGUAGES</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 12 }}>
          {targetLanguages.map((lang) => (
            <div key={lang} className="chip">
              {lang}
              <button onClick={() => setTargetLanguages(targetLanguages.filter((l) => l !== lang))}>×</button>
            </div>
          ))}
        </div>
        <div style={{ display: "flex", gap: 8, marginBottom: 20 }}>
          <input
            type="text" placeholder='Type any language — e.g. "es", "German"…' value={newTargetLang}
            onChange={(e) => setNewTargetLang(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addTargetLanguage()}
            style={{ flex: 1 }}
          />
          <button className="btn-ghost" onClick={addTargetLanguage}>+ Add</button>
        </div>

        <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>OUTPUT FORMATS</div>
        <div style={{ display: "flex", gap: 16, marginBottom: 20 }}>
          {["srt", "vtt", "webvtt"].map((fmt) => (
            <label key={fmt} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, fontWeight: 600 }}>
              <input type="checkbox" checked={outputFormats.includes(fmt)} onChange={() => toggleFormat(fmt)} />
              {fmt.toUpperCase()}
            </label>
          ))}
        </div>

        <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
          TVDB SERIES ID (OPTIONAL — auto-protects the real cast's names from mistranslation)
        </div>
        <input
          type="text" inputMode="numeric" value={tvdbId} onChange={(e) => setTvdbId(e.target.value.replace(/\D/g, ""))}
          placeholder="e.g. 383383 — from thetvdb.com/series/... or a library folder's {tvdb-XXXXX} tag"
          style={{ width: "100%", marginBottom: 20 }}
        />

        <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
          OPTIONAL GLOSSARY (JSON — adds to the TVDB cast list above; a matching name here wins)
        </div>
        <textarea
          value={glossaryText} onChange={(e) => setGlossaryText(e.target.value)}
          placeholder='[{"canonical": "Eda", "aliases": ["Edacim"]}]'
          style={{
            width: "100%", minHeight: 70, background: "var(--card-soft)", border: "1px solid var(--border)",
            borderRadius: 9, padding: 10, color: "var(--text)", fontFamily: "monospace", fontSize: 12, marginBottom: 20,
          }}
        />

        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <button className="btn btn-primary" onClick={submit} disabled={submitting || !file || targetLanguages.length === 0}>
            {submitting ? "Submitting…" : "Translate"}
          </button>
        </div>
      </div>
    </div>
  );
}
