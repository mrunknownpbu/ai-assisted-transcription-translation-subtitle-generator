"use strict";

const state = {
  browsePath: "",
  selected: null,   // { path, filename, duration, audio_tracks, existing_subtitles }
  overwrite: { original: false, english: false },
  jobFilter: "ALL",
  expandedJobId: null,
  recommendedStream: null,
};

const el = (id) => document.getElementById(id);

async function api(path, options) {
  const res = await fetch(path, options);
  let body = null;
  try { body = await res.json(); } catch (_) { /* no body */ }
  if (!res.ok) {
    const detail = (body && body.detail) || res.statusText;
    throw new Error(detail);
  }
  return body;
}

function fmtDuration(seconds) {
  if (!seconds) return "—";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}h ${m}m ${sec}s` : `${m}m ${sec}s`;
}

function fmtElapsed(seconds) {
  if (seconds == null) return "—";
  const s = Math.round(seconds);
  const m = Math.floor(s / 60), sec = s % 60;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

function fmtBytes(bytes) {
  if (!bytes) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0, v = bytes;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

// ---- Browser ----

async function loadBrowser(path) {
  state.browsePath = path;
  el("browser").textContent = "Loading…";
  try {
    const data = await api(`/api/browse?path=${encodeURIComponent(path)}`);
    renderCrumbs(data.path);
    renderBrowser(data.entries);
  } catch (err) {
    el("browser").textContent = `Failed to load: ${err.message}`;
  }
}

function renderCrumbs(path) {
  const crumbs = el("crumbs");
  crumbs.innerHTML = "";
  const root = document.createElement("button");
  root.className = "crumb";
  root.textContent = "Library";
  root.onclick = () => loadBrowser("");
  crumbs.appendChild(root);
  if (!path) return;
  const parts = path.split("/");
  let acc = "";
  for (const part of parts) {
    acc = acc ? `${acc}/${part}` : part;
    const span = document.createElement("span");
    span.textContent = " / ";
    crumbs.appendChild(span);
    const btn = document.createElement("button");
    btn.className = "crumb";
    btn.textContent = part;
    const target = acc;
    btn.onclick = () => loadBrowser(target);
    crumbs.appendChild(btn);
  }
}

function renderBrowser(entries) {
  const container = el("browser");
  container.innerHTML = "";
  if (!entries.length) {
    container.textContent = "This folder is empty.";
    return;
  }
  for (const entry of entries) {
    const row = document.createElement("div");
    row.className = `browser-row ${entry.type}`;
    if (entry.type === "directory") {
      row.textContent = `\u{1F4C1} ${entry.name}`;
      row.onclick = () => loadBrowser(entry.path);
    } else {
      row.textContent = `\u{1F3AC} ${entry.name}`;
      row.title = fmtBytes(entry.size);
      row.onclick = () => selectVideo(entry.path);
      if (state.selected && state.selected.path === entry.path) row.classList.add("selected");
    }
    container.appendChild(row);
  }
}

el("up").onclick = () => {
  if (!state.browsePath) return;
  const parts = state.browsePath.split("/");
  parts.pop();
  loadBrowser(parts.join("/"));
};

// ---- Selection ----

async function selectVideo(path) {
  el("selection").textContent = "Loading…";
  try {
    const meta = await api(`/api/media?path=${encodeURIComponent(path)}`);
    state.selected = meta;
    state.overwrite = { original: false, english: false };
    state.recommendedStream = null;
    renderSelection();
    await loadBrowser(state.browsePath);   // refresh to highlight selection
  } catch (err) {
    el("selection").textContent = `Could not read this file: ${err.message}`;
    el("start").disabled = true;
  }
}

function streamLabel(t) {
  const bits = [`#${t.index}`, t.language || "und"];
  if (t.default) bits.push("default");
  if (t.detected_language) {
    const pct = t.detection_confidence != null ? ` ${Math.round(t.detection_confidence * 100)}%` : "";
    bits.push(`detected ${t.detected_language}${pct}`);
  }
  if (t.exclusion_reason) bits.push(t.exclusion_reason);
  return bits.join(" · ");
}

function streamCardHtml(t) {
  const flags = [];
  if (t.default) flags.push("Default");
  if (t.commentary) flags.push("Commentary");
  if (t.visual_impaired) flags.push("Audio description");
  if (t.hearing_impaired) flags.push("Hearing-impaired");
  const detected = t.detected_language
    ? `<div class="option-row"><span>Detected</span><strong>${t.detected_language}${t.detection_confidence != null ? ` (${Math.round(t.detection_confidence * 100)}%)` : ""}</strong></div>`
    : "";
  const recommended = t.index === state.recommendedStream;
  return `
    <div class="stream-card${recommended ? " recommended" : ""}">
      <div class="stream-card-title">#${t.index} ${t.title || t.language || "unknown"}${recommended ? " ★ recommended" : ""}</div>
      <div class="option-row"><span>Codec</span><strong>${t.codec || "?"}</strong></div>
      <div class="option-row"><span>Channels</span><strong>${t.channels || "?"}${t.channel_layout ? ` (${t.channel_layout})` : ""}</strong></div>
      <div class="option-row"><span>Embedded language</span><strong>${t.language || "und"}</strong></div>
      ${detected}
      ${flags.length ? `<div class="option-row"><span>Flags</span><strong>${flags.join(", ")}</strong></div>` : ""}
    </div>`;
}

function renderAudioStreams() {
  const tracks = state.selected.audio_tracks || [];
  el("audio-streams").classList.toggle("hidden", tracks.length === 0);
  el("audio-stream-list").innerHTML = tracks.map(streamCardHtml).join("") || "<p>No audio streams found.</p>";
  const select = el("audio-stream-select");
  const previous = select.value;
  select.innerHTML = '<option value="">Auto (recommended)</option>';
  for (const t of tracks) {
    const opt = document.createElement("option");
    opt.value = t.index;
    opt.textContent = streamLabel(t);
    select.appendChild(opt);
  }
  select.value = tracks.some(t => String(t.index) === previous) ? previous : "";
  updateDerivedLanguageDisplay();
}

const LOW_CONFIDENCE_THRESHOLD = 0.5;   // matches pipeline.py's DEFAULT_LOW_CONFIDENCE_THRESHOLD

function selectedStreamTrack() {
  const value = el("audio-stream-select").value;
  if (value === "" || !state.selected) return null;
  const index = parseInt(value, 10);
  return (state.selected.audio_tracks || []).find(t => t.index === index) || null;
}

// ALWAYS "auto" -- this used to forward a stream's Analyze-detected
// language as a forced manual source_lang once its confidence cleared
// LOW_CONFIDENCE_THRESHOLD, on the theory that a confident-enough sample
// was as trustworthy as a real detection. A real production run (S01E02,
// genuinely Turkish, embedded tag 'tur') disproved that: the short-sample
// Analyze call returned "nn" (Norwegian Nynorsk) at 61% confidence --
// above the threshold -- which would have forced the ASR to misdecode
// the entire episode. Full-episode AUTO detection on the exact same
// audio correctly found "tr" at 99.8%. The two confidences are not on
// the same scale: a short sample and a full-episode pass are different
// reliability regimes, and no single threshold value makes the short one
// safe to force as a manual override. There is also no scenario where
// forcing it helps: when the sample agrees with reality, AUTO finds the
// same answer anyway (see the earlier Hindi/Malayalam validation, and
// S01E01 in this same project); when the sample is wrong, AUTO is still
// likely right while a forced override locks in the mistake. So this
// function no longer even looks at the selected stream or Analyze
// results -- see updateDerivedLanguageDisplay() below for where that
// data still belongs: informational display only, to help pick the
// right STREAM among several candidates, never to set what language ASR
// is told to assume.
function deriveSourceLang() {
  return "auto";
}

function updateDerivedLanguageDisplay() {
  const target = el("derived-language");
  if (!target) return;
  const track = selectedStreamTrack();
  if (track && track.detected_language != null && track.detection_confidence != null) {
    const pct = Math.round(track.detection_confidence * 100);
    target.textContent = track.detection_confidence >= LOW_CONFIDENCE_THRESHOLD
      ? `${track.detected_language.toUpperCase()} (${pct}% detected)`
      : `Auto-detect (${track.detected_language.toUpperCase()} guess only ${pct}% confident)`;
  } else {
    target.textContent = "Auto-detect";
  }
}

el("audio-stream-select").onchange = updateDerivedLanguageDisplay;

el("analyze-audio").onclick = async () => {
  if (!state.selected) return;
  const btn = el("analyze-audio");
  btn.disabled = true;
  const recEl = el("audio-stream-recommendation");
  recEl.classList.remove("hidden");
  recEl.textContent = "Analyzing audio streams (this samples each candidate track)…";
  try {
    const data = await api(`/api/audio-streams?path=${encodeURIComponent(state.selected.path)}`);
    state.recommendedStream = data.recommended_index;
    const detectionByIndex = Object.fromEntries(data.ranked.map(r => [r.index, r]));
    state.selected.audio_tracks = state.selected.audio_tracks.map(t => ({
      ...t,
      detected_language: detectionByIndex[t.index]?.language ?? t.detected_language,
      detection_confidence: detectionByIndex[t.index]?.confidence ?? t.detection_confidence,
    }));
    renderAudioStreams();
    const altText = data.alternates.length
      ? ` Alternate(s): ${data.alternates.map(a => `#${a.index} ${a.language} ${Math.round((a.confidence || 0) * 100)}%`).join(", ")}.`
      : "";
    const pct = data.recommended_confidence != null ? ` (${Math.round(data.recommended_confidence * 100)}%)` : "";
    recEl.textContent = `Recommended: #${data.recommended_index} ${data.recommended_language || "?"}${pct} — ${data.reason}.${altText}`;
  } catch (err) {
    recEl.textContent = `Could not analyze audio streams: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
};

function renderSelection() {
  const meta = state.selected;
  el("selection").innerHTML = "";
  const summary = el("summary");
  summary.classList.remove("hidden");
  summary.innerHTML = `
    <div class="option-row"><span>File</span><strong>${meta.filename}</strong></div>
    <div class="option-row"><span>Duration</span><strong>${fmtDuration(meta.duration)}</strong></div>
    <div class="option-row"><span>Size</span><strong>${fmtBytes(meta.size)}</strong></div>
  `;
  renderAudioStreams();
  const existingEl = el("existing");
  if (meta.existing_subtitles.length) {
    existingEl.classList.remove("hidden");
    existingEl.innerHTML = "<p class=\"retry-note\">Existing AI subtitles found. Choose whether to keep or replace each one.</p>";
    const wantsOriginal = meta.existing_subtitles.some(s => !s.endsWith(".en.srt"));
    const wantsEnglish = meta.existing_subtitles.some(s => s.endsWith(".en.srt"));
    if (wantsOriginal) existingEl.appendChild(overwriteToggle("original", "Original-language transcript"));
    if (wantsEnglish) existingEl.appendChild(overwriteToggle("english", "English translation"));
  } else {
    existingEl.classList.add("hidden");
    existingEl.innerHTML = "";
  }
  el("start").disabled = false;
}

function overwriteToggle(key, label) {
  const row = document.createElement("label");
  row.className = "existing-row";
  const select = document.createElement("select");
  select.innerHTML = `<option value="keep">Keep existing</option><option value="replace">Replace</option>`;
  select.onchange = () => { state.overwrite[key] = select.value === "replace"; };
  row.textContent = label + " ";
  row.appendChild(select);
  return row;
}

el("clear").onclick = () => {
  state.selected = null;
  state.recommendedStream = null;
  el("selection").textContent = "Select a video from the library.";
  el("summary").classList.add("hidden");
  el("existing").classList.add("hidden");
  el("audio-streams").classList.add("hidden");
  el("audio-stream-recommendation").classList.add("hidden");
  el("derived-language").textContent = "Auto-detect";
  el("start").disabled = true;
  loadBrowser(state.browsePath);
};

el("start").onclick = async () => {
  if (!state.selected) return;
  el("start").disabled = true;
  el("message").textContent = "";
  try {
    const streamValue = el("audio-stream-select").value;
    await api("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        video_path: state.selected.path,
        source_lang: deriveSourceLang(),
        audio_stream_index: streamValue === "" ? null : parseInt(streamValue, 10),
        overwrite_original: state.overwrite.original,
        overwrite_english: state.overwrite.english,
      }),
    });
    el("message").textContent = "Job queued.";
    el("message").className = "message ok";
    loadJobs();
  } catch (err) {
    el("message").textContent = err.message;
    el("message").className = "message error";
  } finally {
    el("start").disabled = false;
  }
};

// ---- Jobs ----

const TABS = ["ALL", "QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "SKIPPED"];

function renderTabs() {
  const tabs = el("tabs");
  tabs.innerHTML = "";
  for (const tab of TABS) {
    const btn = document.createElement("button");
    btn.className = "tab" + (state.jobFilter === tab ? " active" : "");
    btn.textContent = tab;
    btn.onclick = () => { state.jobFilter = tab; renderTabs(); loadJobs(); };
    tabs.appendChild(btn);
  }
}

async function loadQueue() {
  try {
    const counts = await api("/api/queue");
    el("queue-summary").textContent =
      `${counts.QUEUED} queued · ${counts.RUNNING} running · ${counts.COMPLETED} completed · ${counts.FAILED} failed`;
  } catch (_) {
    el("queue-summary").textContent = "Queue status unavailable";
  }
}

function languageAnnotation(job) {
  if (job.source_language_mode !== "AUTO") return "";
  if (job.detected_language) {
    const pct = job.language_confidence != null ? ` ${Math.round(job.language_confidence * 100)}%` : "";
    return ` · detected ${job.detected_language.toUpperCase()}${pct}`;
  }
  return (job.status === "running" || job.status === "queued") ? " · detecting…" : "";
}

function qcSummary(qc) {
  if (!qc || !Object.keys(qc).length) return "—";
  const total = Object.values(qc).reduce((sum, stage) => sum + (stage.flagged || 0), 0);
  return total === 0 ? "clean" : `${total} flagged`;
}

async function loadJobs() {
  try {
    const status = state.jobFilter === "ALL" ? null : state.jobFilter.toLowerCase();
    const url = status ? `/api/jobs?status=${status}&limit=100` : "/api/jobs?limit=100";
    const data = await api(url);
    renderJobs(data.jobs);
  } catch (err) {
    el("jobs").innerHTML = `<tr><td colspan="7">Failed to load jobs: ${err.message}</td></tr>`;
  }
  loadQueue();
}

function renderJobs(jobs) {
  const tbody = el("jobs");
  tbody.innerHTML = "";
  if (!jobs.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty">No jobs in this view.</td></tr>`;
    return;
  }
  for (const job of jobs) {
    const tr = document.createElement("tr");
    tr.className = `status-${job.status}`;
    tr.innerHTML = `
      <td class="file-cell" title="${job.video_path}">${job.video_path}</td>
      <td>${job.status}</td>
      <td>${job.stage}${languageAnnotation(job)}</td>
      <td>${job.progress}%</td>
      <td>${fmtElapsed(job.elapsed_seconds)}</td>
      <td>${qcSummary(job.qc)}</td>
      <td class="actions"></td>
    `;
    const actions = tr.querySelector(".actions");
    if (job.status === "queued" || job.status === "running") {
      actions.appendChild(actionButton("Cancel", () => cancelJob(job.id)));
    }
    if (["completed", "failed", "cancelled", "skipped"].includes(job.status)) {
      actions.appendChild(actionButton("Retry", () => retryJob(job.id)));
      actions.appendChild(actionButton("Delete", () => deleteJob(job.id), job.status === "completed"));
    }
    actions.appendChild(actionButton("Log", () => toggleDetail(job)));
    tbody.appendChild(tr);
  }
}

function actionButton(label, onClick, danger) {
  const btn = document.createElement("button");
  btn.className = "text-button" + (danger ? " danger" : "");
  btn.textContent = label;
  btn.onclick = onClick;
  return btn;
}

async function cancelJob(id) {
  try { await api(`/api/jobs/${id}/cancel`, { method: "POST" }); loadJobs(); }
  catch (err) { alert(`Could not cancel: ${err.message}`); }
}

async function retryJob(id) {
  try {
    await api(`/api/jobs/${id}/retry`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    loadJobs();
  } catch (err) { alert(`Could not retry: ${err.message}`); }
}

async function deleteJob(id) {
  try { await api(`/api/jobs/${id}`, { method: "DELETE" }); loadJobs(); }
  catch (err) { alert(`Could not delete: ${err.message}`); }
}

function toggleDetail(job) {
  const detail = el("detail");
  if (state.expandedJobId === job.id) {
    state.expandedJobId = null;
    detail.classList.add("hidden");
    detail.innerHTML = "";
    return;
  }
  state.expandedJobId = job.id;
  detail.classList.remove("hidden");
  const log = (job.log || []).map(entry => `<div>${new Date(entry.time * 1000).toLocaleTimeString()} — ${entry.message}</div>`).join("");
  const requested = (job.source_lang || "auto").toUpperCase();
  const mode = job.source_language_mode || "AUTO";
  const detected = job.detected_language
    ? `, detected=${job.detected_language.toUpperCase()}${job.language_confidence != null ? ` (${Math.round(job.language_confidence * 100)}% confidence)` : ""}`
    : "";
  const target = (job.target_lang || "en").toUpperCase();
  const streamMode = job.stream_selection_mode || "AUTO";
  const streamIdx = job.selected_audio_stream != null ? `#${job.selected_audio_stream}` : "not yet selected";
  const embedded = job.embedded_stream_language ? ` (embedded: ${job.embedded_stream_language})` : "";
  const streamReason = job.selected_stream_reason ? ` — ${job.selected_stream_reason}` : "";
  detail.innerHTML = `
    <h3>${job.video_path}</h3>
    <p class="lang-info">Spoken language: requested=${requested} (${mode})${detected} &rarr; target=${target}</p>
    <p class="lang-info">Audio stream: ${streamIdx}${embedded} (${streamMode})${streamReason}</p>
    ${job.error ? `<p class="error">${job.error_category || "ERROR"}: ${job.error}</p>` : ""}
    <div class="log">${log || "No log entries."}</div>
  `;
}

el("refresh").onclick = loadJobs;

// ---- Init ----

renderTabs();
loadBrowser("");
loadJobs();
setInterval(loadJobs, 2000);
