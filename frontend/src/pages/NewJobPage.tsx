import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { AudioStream, LanguageDetection, LibraryEntry, MediaFile } from "../types";

const STEPS = ["Select Media", "Audio Stream", "Language", "Output & Submit"];

function StepWizard({ current }: { current: number }) {
  return (
    <div className="card step-wizard">
      {STEPS.map((label, i) => (
        <div key={label} style={{ display: "flex", alignItems: "center" }}>
          <div className="step">
            <div className={`step-num ${i < current ? "done" : i === current ? "active" : ""}`}>
              {i < current ? "✓" : i + 1}
            </div>
            <div className={`step-label ${i <= current ? "active" : ""}`}>{label}</div>
          </div>
          {i < STEPS.length - 1 && <div className={`step-line ${i < current ? "done" : ""}`} />}
        </div>
      ))}
    </div>
  );
}

export default function JobWizard() {
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [step, setStep] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [media, setMedia] = useState<MediaFile | null>(null);
  const [selectedStreamIndex, setSelectedStreamIndex] = useState<number | null>(null);
  const [languageDetection, setLanguageDetection] = useState<LanguageDetection | null>(null);
  const [sourceLanguageOverride, setSourceLanguageOverride] = useState("");
  const [targetLanguages, setTargetLanguages] = useState<string[]>([]);
  const [newTargetLang, setNewTargetLang] = useState("");
  const [outputFormats, setOutputFormats] = useState<string[]>(["srt", "vtt", "webvtt"]);
  const [submitting, setSubmitting] = useState(false);

  const [sourceMode, setSourceMode] = useState<"upload" | "library">("upload");
  const [libraryAvailable, setLibraryAvailable] = useState(false);
  const [libraryPath, setLibraryPath] = useState("");
  const [libraryEntries, setLibraryEntries] = useState<LibraryEntry[]>([]);
  const [libraryLoading, setLibraryLoading] = useState(false);

  useEffect(() => {
    api.browseLibrary("").then(() => setLibraryAvailable(true)).catch(() => setLibraryAvailable(false));
  }, []);

  async function loadLibraryPath(path: string) {
    setLibraryLoading(true);
    setError(null);
    try {
      const entries = await api.browseLibrary(path);
      setLibraryPath(path);
      setLibraryEntries(entries);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLibraryLoading(false);
    }
  }

  async function handleFileSelected(file: File) {
    setUploading(true);
    setError(null);
    try {
      const uploaded = await api.uploadMedia(file);
      setMedia(uploaded);
      const auto = uploaded.audio_streams.find((s) => s.selected_by === "auto");
      setSelectedStreamIndex(auto ? auto.stream_index : uploaded.audio_streams[0]?.stream_index ?? null);
      setStep(1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUploading(false);
    }
  }

  async function handleLibrarySelected(entry: LibraryEntry) {
    setUploading(true);
    setError(null);
    try {
      const registered = await api.registerLibraryMedia(entry.path);
      setMedia(registered);
      const auto = registered.audio_streams.find((s) => s.selected_by === "auto");
      setSelectedStreamIndex(auto ? auto.stream_index : registered.audio_streams[0]?.stream_index ?? null);
      setStep(1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUploading(false);
    }
  }

  async function confirmStreamAndDetectLanguage() {
    if (!media || selectedStreamIndex === null) return;
    const chosen = media.audio_streams.find((s) => s.stream_index === selectedStreamIndex)!;
    setError(null);
    try {
      if (chosen.selected_by !== "auto") {
        await api.selectStream(media.id, selectedStreamIndex);
      }
      const detection = await api.detectLanguage(media.id, chosen.id);
      setLanguageDetection(detection);
      setStep(2);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function addTargetLanguage() {
    const value = newTargetLang.trim();
    if (value && !targetLanguages.includes(value)) {
      setTargetLanguages([...targetLanguages, value]);
    }
    setNewTargetLang("");
  }

  function toggleFormat(fmt: string) {
    setOutputFormats((prev) => (prev.includes(fmt) ? prev.filter((f) => f !== fmt) : [...prev, fmt]));
  }

  async function submitJob() {
    if (!media || selectedStreamIndex === null) return;
    const stream = media.audio_streams.find((s) => s.stream_index === selectedStreamIndex)!;
    setSubmitting(true);
    setError(null);
    try {
      const job = await api.createJob({
        media_file_id: media.id,
        audio_stream_id: stream.id,
        source_language: sourceLanguageOverride || null,
        target_languages: targetLanguages,
        output_formats: outputFormats,
      });
      navigate(`/jobs/${job.id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="wizard-stack">
      <StepWizard current={step} />
      {error && <div className="error-banner">{error}</div>}

      {step === 0 && (
        <div className="card" style={{ padding: 22 }}>
          <div style={{ fontSize: 15, fontWeight: 800, marginBottom: 4 }}>1. Select Media File</div>
          <div className="muted" style={{ fontSize: 12, marginBottom: 16 }}>
            {sourceMode === "upload"
              ? "Upload a media file to inspect its audio streams. Any format ffmpeg can read is supported, with no file size limit enforced by the platform."
              : "Pick a file already in the media library — read directly, never copied."}
          </div>

          {libraryAvailable && (
            <div style={{ display: "flex", gap: 4, background: "var(--card-soft)", border: "1px solid var(--border)", borderRadius: 10, padding: 4, marginBottom: 16, width: "fit-content" }}>
              <button
                className={sourceMode === "upload" ? "btn btn-primary" : "btn-ghost"}
                style={{ padding: "7px 14px" }}
                onClick={() => setSourceMode("upload")}
              >
                Upload
              </button>
              <button
                className={sourceMode === "library" ? "btn btn-primary" : "btn-ghost"}
                style={{ padding: "7px 14px" }}
                onClick={() => { setSourceMode("library"); if (libraryEntries.length === 0) loadLibraryPath(""); }}
              >
                Browse Library
              </button>
            </div>
          )}

          {sourceMode === "upload" && (
            <>
              <div
                onClick={() => fileInputRef.current?.click()}
                style={{
                  border: "1.5px dashed var(--border)", borderRadius: 12, padding: "48px 0", textAlign: "center",
                  cursor: "pointer", background: "var(--card-soft)",
                }}
              >
                {uploading ? "Uploading & inspecting…" : "Click to browse, or drag & drop a media file here"}
              </div>
              <input
                ref={fileInputRef} type="file" style={{ display: "none" }}
                onChange={(e) => e.target.files?.[0] && handleFileSelected(e.target.files[0])}
              />
            </>
          )}

          {sourceMode === "library" && (
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, fontSize: 12 }}>
                <button className="btn-ghost" disabled={!libraryPath} onClick={() => {
                  const parent = libraryPath.split("/").slice(0, -1).join("/");
                  loadLibraryPath(parent);
                }}>
                  ↑ Up
                </button>
                <span className="mono muted">/{libraryPath}</span>
              </div>
              <div style={{ border: "1px solid var(--border-soft)", borderRadius: 10, overflow: "hidden" }}>
                {libraryLoading ? (
                  <div style={{ padding: 16, textAlign: "center" }} className="muted">Loading…</div>
                ) : libraryEntries.length === 0 ? (
                  <div style={{ padding: 16, textAlign: "center" }} className="muted">Empty directory</div>
                ) : (
                  libraryEntries.map((entry) => (
                    <div
                      key={entry.path}
                      onClick={() => (entry.is_dir ? loadLibraryPath(entry.path) : handleLibrarySelected(entry))}
                      style={{
                        padding: "10px 14px", cursor: "pointer", fontSize: 13, display: "flex", alignItems: "center", gap: 8,
                        borderBottom: "1px solid var(--border-soft)",
                      }}
                    >
                      {entry.is_dir ? (
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--text-mute)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6.5A1.5 1.5 0 0 1 5.5 5h4l2 2.5h7A1.5 1.5 0 0 1 20 9v8.5A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5v-11Z"/></svg>
                      ) : (
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--blue)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 5v14l11-7Z"/></svg>
                      )}
                      {entry.name}
                    </div>
                  ))
                )}
              </div>
              {uploading && <div className="muted" style={{ marginTop: 10, fontSize: 12 }}>Registering &amp; inspecting…</div>}
            </div>
          )}
        </div>
      )}

      {step === 1 && media && (
        <div className="card" style={{ padding: 22 }}>
          <div style={{ fontSize: 15, fontWeight: 800, marginBottom: 4 }}>2. Select Audio Stream</div>
          <div className="muted" style={{ fontSize: 12, marginBottom: 16 }}>
            Ranked automatically from the actual waveform of each stream — never from track titles or language tags.
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {media.audio_streams
              .slice()
              .sort((a, b) => (a.dialogue_rank ?? 99) - (b.dialogue_rank ?? 99))
              .map((s: AudioStream) => (
                <label
                  key={s.id}
                  className="card"
                  style={{
                    padding: 14, display: "flex", alignItems: "center", gap: 16, cursor: "pointer",
                    borderColor: selectedStreamIndex === s.stream_index ? "var(--blue)" : undefined,
                  }}
                >
                  <input
                    type="radio" name="stream" checked={selectedStreamIndex === s.stream_index}
                    onChange={() => setSelectedStreamIndex(s.stream_index)}
                  />
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 700, fontSize: 13.5 }}>
                      Stream #{s.stream_index} {s.dialogue_rank === 1 && <span className="pill" style={{ background: "var(--blue-soft)", color: "var(--blue)", marginLeft: 8 }}>Auto-Selected</span>}
                    </div>
                    <div className="mono muted" style={{ fontSize: 11, marginTop: 2 }}>
                      {s.codec} · {s.channels}ch · {s.sample_rate}Hz
                    </div>
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <div style={{ fontSize: 18, fontWeight: 800, color: "var(--blue)" }}>{s.dialogue_score?.toFixed(2) ?? "—"}</div>
                    <div className="muted" style={{ fontSize: 10 }}>Dialogue Score</div>
                  </div>
                </label>
              ))}
          </div>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 18 }}>
            <button className="btn-ghost" onClick={() => setStep(0)}>Back</button>
            <button className="btn btn-primary" onClick={confirmStreamAndDetectLanguage} disabled={selectedStreamIndex === null}>
              Continue to Language
            </button>
          </div>
        </div>
      )}

      {step === 2 && languageDetection && (
        <div className="card" style={{ padding: 22 }}>
          <div style={{ fontSize: 15, fontWeight: 800, marginBottom: 16 }}>3. Language &amp; Translation</div>

          <div style={{ display: "flex", gap: 16, alignItems: "center", marginBottom: 20 }}>
            <div className="card" style={{ padding: 14, flex: 1, background: "var(--card-soft)" }}>
              <div className="muted" style={{ fontSize: 11 }}>DETECTED SOURCE LANGUAGE</div>
              <div style={{ fontSize: 18, fontWeight: 800 }}>{languageDetection.detected_language}</div>
              <div className="muted" style={{ fontSize: 11 }}>
                {(languageDetection.confidence * 100).toFixed(0)}% confidence · {languageDetection.method}
              </div>
            </div>
            <div style={{ width: 220 }}>
              <div className="muted" style={{ fontSize: 11, marginBottom: 6 }}>OVERRIDE (OPTIONAL)</div>
              <input
                type="text" placeholder="e.g. es, French…" value={sourceLanguageOverride}
                onChange={(e) => setSourceLanguageOverride(e.target.value)} style={{ width: "100%" }}
              />
            </div>
          </div>

          <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>TARGET LANGUAGES (ANY LANGUAGE, ANY NUMBER)</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 12 }}>
            {targetLanguages.map((lang) => (
              <div key={lang} className="chip">
                {lang}
                <button onClick={() => setTargetLanguages(targetLanguages.filter((l) => l !== lang))}>×</button>
              </div>
            ))}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              type="text" placeholder='Type any language — e.g. "Yoruba", "fr", "German"…' value={newTargetLang}
              onChange={(e) => setNewTargetLang(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addTargetLanguage()}
              style={{ flex: 1 }}
            />
            <button className="btn-ghost" onClick={addTargetLanguage}>+ Add</button>
          </div>

          <div className="muted" style={{ fontSize: 11, margin: "20px 0 8px" }}>OUTPUT FORMATS</div>
          <div style={{ display: "flex", gap: 16 }}>
            {["srt", "vtt", "webvtt", "burned_in"].map((fmt) => (
              <label key={fmt} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, fontWeight: 600 }}>
                <input type="checkbox" checked={outputFormats.includes(fmt)} onChange={() => toggleFormat(fmt)} />
                {fmt === "burned_in" ? "Burned-in Video" : fmt.toUpperCase()}
              </label>
            ))}
          </div>

          <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 22 }}>
            <button className="btn-ghost" onClick={() => setStep(1)}>Back</button>
            <button className="btn btn-primary" onClick={() => setStep(3)} disabled={targetLanguages.length === 0}>
              Continue
            </button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="card" style={{ padding: 22 }}>
          <div style={{ fontSize: 15, fontWeight: 800, marginBottom: 16 }}>4. Review &amp; Submit</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 13, marginBottom: 20 }}>
            <div><span className="muted">File:</span> {media?.filename}</div>
            <div><span className="muted">Stream:</span> #{selectedStreamIndex}</div>
            <div><span className="muted">Source language:</span> {sourceLanguageOverride || languageDetection?.detected_language} {sourceLanguageOverride && "(manual override)"}</div>
            <div><span className="muted">Target languages:</span> {targetLanguages.join(", ")}</div>
            <div><span className="muted">Output formats:</span> {outputFormats.join(", ")}</div>
          </div>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
            <button className="btn-ghost" onClick={() => setStep(2)}>Back</button>
            <button className="btn btn-primary" onClick={submitJob} disabled={submitting}>
              {submitting ? "Submitting…" : "Start Processing"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
