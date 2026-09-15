import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, subscribeToJobProgress } from "../api/client";
import type { EvaluationReport, Job, JobLogs, LibraryEntry, Output, ReferenceOptions } from "../types";

function scoreColor(score: number): string {
  if (score >= 0.7) return "green";
  if (score >= 0.4) return "amber";
  return "red";
}

const MANUAL_ENTRY = "__manual__";

function ArchiveBrowser({ selectedPath, onSelect }: { selectedPath: string; onSelect: (path: string) => void }) {
  const [dir, setDir] = useState("");
  const [entries, setEntries] = useState<LibraryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [unavailable, setUnavailable] = useState(false);

  async function loadDir(path: string) {
    setLoading(true);
    try {
      const listing = await api.browseReferences(path);
      setDir(path);
      setEntries(listing);
    } catch {
      setUnavailable(true);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadDir(""); }, []);

  if (unavailable) {
    return <div className="muted" style={{ fontSize: 12 }}>No reference archive configured on this deployment.</div>;
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, fontSize: 12 }}>
        <button className="btn-ghost" disabled={!dir} onClick={() => loadDir(dir.split("/").slice(0, -1).join("/"))}>↑ Up</button>
        <span className="mono muted">/{dir}</span>
      </div>
      <div style={{ border: "1px solid var(--border-soft)", borderRadius: 10, overflow: "hidden", maxHeight: 200, overflowY: "auto" }}>
        {loading ? (
          <div style={{ padding: 16, textAlign: "center" }} className="muted">Loading…</div>
        ) : entries.length === 0 ? (
          <div style={{ padding: 16, textAlign: "center" }} className="muted">Empty directory</div>
        ) : (
          entries.map((entry) => (
            <div key={entry.path} onClick={() => (entry.is_dir ? loadDir(entry.path) : onSelect(entry.path))}
                 style={{
                   padding: "8px 12px", cursor: "pointer", fontSize: 12.5, display: "flex", alignItems: "center", gap: 8,
                   borderBottom: "1px solid var(--border-soft)",
                   background: entry.path === selectedPath ? "var(--blue-soft)" : undefined,
                 }}>
              {entry.is_dir ? (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--text-mute)" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d="M4 6.5A1.5 1.5 0 0 1 5.5 5h4l2 2.5h7A1.5 1.5 0 0 1 20 9v8.5A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5v-11Z" /></svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--blue)" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><path d="M14 2v6h6" /></svg>
              )}
              {entry.name}
            </div>
          ))
        )}
      </div>
      {selectedPath && <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>Selected: <span className="mono">{selectedPath}</span></div>}
    </div>
  );
}

function EvaluationPanel({ jobId, mediaFileId, targetLanguages }: { jobId: string; mediaFileId: string | null; targetLanguages: string[] }) {
  const [reports, setReports] = useState<EvaluationReport[]>([]);
  const [options, setOptions] = useState<ReferenceOptions | null>(null);
  const [mode, setMode] = useState<"sidecar" | "embedded" | "archive">("sidecar");
  const [selectedSidecar, setSelectedSidecar] = useState<string>(MANUAL_ENTRY);
  const [manualPath, setManualPath] = useState("");
  const [selectedStreamIndex, setSelectedStreamIndex] = useState<string>("");
  const [selectedArchivePath, setSelectedArchivePath] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    api.listEvaluations(jobId).then(setReports).catch(() => {});
  }, [jobId]);

  useEffect(() => {
    if (!mediaFileId) return;
    api.getReferenceOptions(mediaFileId).then((opts) => {
      setOptions(opts);
      if (opts.embedded_subtitle_streams.length > 0) setMode("embedded");
      if (opts.embedded_subtitle_streams.length > 0) setSelectedStreamIndex(String(opts.embedded_subtitle_streams[0].index));
      if (opts.sibling_subtitle_files.length > 0) setSelectedSidecar(opts.sibling_subtitle_files[0].path);
    }).catch(() => setOptions(null));
  }, [mediaFileId]);

  const referencePath = selectedSidecar === MANUAL_ENTRY ? manualPath : selectedSidecar;

  async function runEvaluation() {
    setRunning(true);
    setError(null);
    try {
      const payload = mode === "sidecar"
        ? { reference_srt_path: referencePath, target_language: targetLanguages[0] }
        : mode === "embedded"
        ? { embedded_stream_index: Number(selectedStreamIndex), target_language: targetLanguages[0] }
        : { reference_archive_path: selectedArchivePath, target_language: targetLanguages[0] };
      const report = await api.evaluateJob(jobId, payload);
      setReports((prev) => [report, ...prev]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  const hasEmbeddedOptions = (options?.embedded_subtitle_streams.length ?? 0) > 0;
  const hasSidecarOptions = (options?.sibling_subtitle_files.length ?? 0) > 0;
  const canEvaluate = mode === "sidecar" ? !!referencePath : mode === "embedded" ? !!selectedStreamIndex : !!selectedArchivePath;

  return (
    <div className="card" style={{ padding: 18 }}>
      <div style={{ fontWeight: 800, fontSize: 13.5, marginBottom: 4 }}>Evaluate Translation Accuracy</div>
      <div className="muted" style={{ fontSize: 11.5, marginBottom: 12 }}>
        Compares this job's output against a real reference subtitle (an embedded mkv stream, a sidecar .srt next
        to the source, or a file from a separate reference archive) — a chrF score measures closeness of the
        actual text, never a model change.
      </div>

      <div style={{ display: "flex", gap: 4, marginBottom: 10 }}>
        <button className={mode === "sidecar" ? "btn btn-primary" : "btn-ghost"} style={{ padding: "6px 12px" }} onClick={() => setMode("sidecar")}>Sidecar file</button>
        <button className={mode === "embedded" ? "btn btn-primary" : "btn-ghost"} style={{ padding: "6px 12px" }} onClick={() => setMode("embedded")}>Embedded stream</button>
        <button className={mode === "archive" ? "btn btn-primary" : "btn-ghost"} style={{ padding: "6px 12px" }} onClick={() => setMode("archive")}>Reference archive</button>
      </div>

      <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
        {mode === "sidecar" ? (
          <>
            <select style={{ flex: 1, minWidth: 240 }} value={selectedSidecar} onChange={(e) => setSelectedSidecar(e.target.value)}>
              {options?.sibling_subtitle_files.map((f) => <option key={f.path} value={f.path}>{f.name}</option>)}
              <option value={MANUAL_ENTRY}>{hasSidecarOptions ? "Other (type a path)…" : "No sidecar subtitles found nearby — type a path"}</option>
            </select>
            {selectedSidecar === MANUAL_ENTRY && (
              <input type="text" style={{ flex: 1, minWidth: 240 }}
                     placeholder="Path relative to library root, e.g. drama/chinese/Show/Season 01/Show S01E01.en.hi.srt"
                     value={manualPath} onChange={(e) => setManualPath(e.target.value)} />
            )}
          </>
        ) : mode === "embedded" ? (
          hasEmbeddedOptions ? (
            <select style={{ minWidth: 260 }} value={selectedStreamIndex} onChange={(e) => setSelectedStreamIndex(e.target.value)}>
              {options!.embedded_subtitle_streams.map((s) => (
                <option key={s.index} value={s.index}>
                  #{s.index} — {s.language ?? "unknown"} ({s.codec_name})
                </option>
              ))}
            </select>
          ) : (
            <input type="text" style={{ width: 140 }} placeholder="Stream index" value={selectedStreamIndex} onChange={(e) => setSelectedStreamIndex(e.target.value)} />
          )
        ) : (
          <div style={{ flex: 1, minWidth: 280 }}>
            <ArchiveBrowser selectedPath={selectedArchivePath} onSelect={setSelectedArchivePath} />
          </div>
        )}
        {mode !== "archive" && (
          <button className="btn btn-primary" onClick={runEvaluation} disabled={running || !canEvaluate}>
            {running ? "Scoring…" : "Evaluate"}
          </button>
        )}
      </div>
      {mode === "archive" && (
        <button className="btn btn-primary" onClick={runEvaluation} disabled={running || !canEvaluate} style={{ marginBottom: 12 }}>
          {running ? "Scoring…" : "Evaluate"}
        </button>
      )}
      {error && <div className="error-banner" style={{ marginBottom: 12 }}>{error}</div>}

      {reports.length === 0 && <div className="muted" style={{ fontSize: 12 }}>No evaluations run yet.</div>}
      {reports.map((r) => (
        <div key={r.id} className="card" style={{ background: "var(--card-soft)", padding: 12, marginBottom: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 16, cursor: "pointer" }} onClick={() => setExpanded(expanded === r.id ? null : r.id)}>
            <div>
              <div className="muted" style={{ fontSize: 10 }}>CHRF</div>
              <div style={{ fontSize: 18, fontWeight: 800, color: `var(--${scoreColor(r.mean_chrf)})` }}>{(r.mean_chrf * 100).toFixed(0)}%</div>
            </div>
            <div>
              <div className="muted" style={{ fontSize: 10 }}>WORD F1</div>
              <div style={{ fontSize: 18, fontWeight: 800, color: `var(--${scoreColor(r.mean_word_f1)})` }}>{(r.mean_word_f1 * 100).toFixed(0)}%</div>
            </div>
            <div>
              <div className="muted" style={{ fontSize: 10 }}>COVERAGE</div>
              <div style={{ fontSize: 14, fontWeight: 700 }}>{r.matched_count}/{r.cue_count} cues</div>
            </div>
            <div style={{ flex: 1, fontSize: 11, color: "var(--text-mute)", textAlign: "right" }} className="mono">{r.reference_source}</div>
          </div>
          {expanded === r.id && (
            <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6, maxHeight: 320, overflow: "auto" }}>
              {r.per_cue.slice().sort((a, b) => a.chrf - b.chrf).map((c) => (
                <div key={c.hypothesis_index} style={{ fontSize: 11.5, padding: 8, background: "var(--card)", borderRadius: 8 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <span className="mono muted">cue #{c.hypothesis_index}</span>
                    <span style={{ fontWeight: 700, color: `var(--${scoreColor(c.chrf)})` }}>chrF {(c.chrf * 100).toFixed(0)}%</span>
                  </div>
                  <div><span className="muted">Ours: </span>{c.hypothesis_text || <em className="muted">(empty)</em>}</div>
                  <div><span className="muted">Ref: </span>{c.reference_text || <em className="muted">(no overlapping reference cue)</em>}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

const PIPELINE_STAGES = [
  "language_detection", "asr", "hallucination_defense", "normalization",
  "translation", "target_segmentation", "timing_projection", "qc_output",
];

export default function JobDetailPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const [job, setJob] = useState<Job | null>(null);
  const [logs, setLogs] = useState<JobLogs | null>(null);
  const [outputs, setOutputs] = useState<Output[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId) return;
    api.getJob(jobId).then(setJob).catch((e) => setError(e.message));
    api.getJobLogs(jobId).then(setLogs).catch(() => {});
    api.getJobOutputs(jobId).then(setOutputs).catch(() => {});

    const unsubscribe = subscribeToJobProgress(jobId, (update) => {
      setJob((prev) => (prev ? { ...prev, ...update } as Job : prev));
      if (["done", "failed", "canceled"].includes(update.status)) {
        api.getJobLogs(jobId).then(setLogs).catch(() => {});
        api.getJobOutputs(jobId).then(setOutputs).catch(() => {});
      }
    });
    return unsubscribe;
  }, [jobId]);

  if (error) return <div className="content"><div className="error-banner">{error}</div></div>;
  if (!job) return <div className="content">Loading…</div>;

  const currentStageIndex = PIPELINE_STAGES.findIndex((s) => job.current_stage?.startsWith(s));

  return (
    <div className="content">
      <div className="card" style={{ padding: 18, display: "flex", alignItems: "center", gap: 16 }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{ fontSize: 16, fontWeight: 800 }}>Job {job.id.slice(0, 8)}</div>
            {job.job_type === "direct_translation" && (
              <span className="pill" style={{ background: "var(--purple-soft)", color: "var(--purple)" }}>
                Translated subtitle file — not verified against audio
              </span>
            )}
          </div>
          <div className="muted" style={{ fontSize: 11.5, marginTop: 3 }}>
            Source: {job.source_language ?? "auto-detect"} · Targets: {job.target_languages.join(", ")}
            {job.display_filename && ` · ${job.display_filename}`}
          </div>
        </div>
        <span className="pill" style={{ background: "var(--blue-soft)", color: "var(--blue)" }}>{job.status}</span>
        {job.status === "running" && (
          <div style={{ width: 160 }}>
            <div className="bar-track"><div className="bar-fill" style={{ width: `${job.progress_pct}%` }} /></div>
          </div>
        )}
      </div>

      {/* Only ever shown for a job that is actually stuck in "failed" -- error_message can
          otherwise hold a stale reason from an earlier retry attempt that has since
          started fresh and is running fine, and showing it there would misleadingly read
          as a live, current fault. */}
      {job.status === "failed" && job.error_message && <div className="error-banner">{job.error_message}</div>}

      <div style={{ display: "flex", gap: 16 }}>
        <div className="card" style={{ width: 300, flexShrink: 0, padding: 18 }}>
          <div style={{ fontWeight: 800, fontSize: 13.5, marginBottom: 12 }}>Pipeline Stages</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {PIPELINE_STAGES.map((stage, i) => (
              <div key={stage} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12.5 }}>
                <span
                  style={{
                    width: 20, height: 20, borderRadius: "50%", flexShrink: 0,
                    background: i < currentStageIndex ? "var(--green)" : i === currentStageIndex ? "var(--blue)" : "var(--card-soft)",
                    border: i > currentStageIndex ? "1.5px solid var(--border)" : "none",
                  }}
                />
                <span style={{ color: i <= currentStageIndex ? "var(--text)" : "var(--text-mute)", fontWeight: i === currentStageIndex ? 700 : 500 }}>
                  {stage.replace(/_/g, " ")}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="card" style={{ padding: 18 }}>
            <div style={{ fontWeight: 800, fontSize: 13.5, marginBottom: 10 }}>Hallucination Suppression Log</div>
            {logs && logs.suppressions.length > 0 ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {logs.suppressions.map((s, i) => (
                  <div key={i} style={{ display: "grid", gridTemplateColumns: "90px 1fr 120px 90px", gap: 10, fontSize: 12, background: "var(--card-soft)", borderRadius: 8, padding: 8 }}>
                    <span className="mono muted">{s.segment_id}</span>
                    <span>{s.reason}</span>
                    <span className="mono muted">{s.method}</span>
                    <span className="pill" style={{ background: s.decision === "suppressed" ? "var(--red-soft)" : "var(--amber-soft)", color: s.decision === "suppressed" ? "var(--red)" : "var(--amber)" }}>
                      {s.decision === "suppressed" ? "Suppressed" : "Kept"}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="muted" style={{ fontSize: 12 }}>No suppression decisions yet.</div>
            )}
          </div>

          <div className="card" style={{ padding: 18 }}>
            <div style={{ fontWeight: 800, fontSize: 13.5, marginBottom: 10 }}>QC Findings</div>
            {logs && logs.qc_reports.length > 0 ? (
              logs.qc_reports.map((report, i) => (
                <div key={i} style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 12.5, fontWeight: 700, marginBottom: 6 }}>
                    {report.target_language} — <span style={{ color: report.passed ? "var(--green)" : "var(--red)" }}>{report.passed ? "Passed" : "Failed"}</span>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {report.findings.filter((f) => !f.passed).map((f, j) => (
                      <span key={j} className="pill" style={{ background: "var(--red-soft)", color: "var(--red)" }}>{f.check}: {f.reason}</span>
                    ))}
                    {report.findings.every((f) => f.passed) && <span className="pill" style={{ background: "var(--green-soft)", color: "var(--green)" }}>All checks passed</span>}
                  </div>
                </div>
              ))
            ) : (
              <div className="muted" style={{ fontSize: 12 }}>Runs after timing projection. Not started yet.</div>
            )}
          </div>

          {job.status === "done" && (
            <EvaluationPanel jobId={job.id} mediaFileId={job.media_file_id} targetLanguages={job.target_languages} />
          )}

          {outputs.length > 0 && (
            <div className="card" style={{ padding: 18 }}>
              <div style={{ fontWeight: 800, fontSize: 13.5, marginBottom: 10 }}>Downloads</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 12 }}>
                {outputs.map((o) => (
                  <div key={o.id} className="card" style={{ padding: 12, background: "var(--card-soft)" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                      <span style={{ fontWeight: 700, fontSize: 13 }}>{o.format.toUpperCase()}</span>
                      <span className="pill" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>{o.target_language}</span>
                    </div>
                    <a href={api.downloadOutputUrl(o.id)} className="btn-ghost" style={{ display: "block", textAlign: "center", textDecoration: "none" }}>
                      Download
                    </a>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
