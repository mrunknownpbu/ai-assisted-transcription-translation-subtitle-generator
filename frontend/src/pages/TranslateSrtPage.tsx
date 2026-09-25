import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { useBrowse, useCreateSrtTranslationJob, useLanguages, useMedia, useUploadSrt } from "../api/hooks";
import type { BrowseEntry } from "../api/types";
import { MediaBrowser } from "../components/MediaBrowser";
import { useToast } from "../components/Toast";

type SourceSelection =
  | { mode: "library"; path: string; filename: string }
  | { mode: "upload"; uploadId: string; filename: string }
  | null;

// English-only suffixes (the translation target itself, plus the
// protected hearing-impaired/forced/SDH variants output.PROTECTED_SUFFIXES
// never touches) -- anything else in existing_subtitles for this video is
// an original-language file. DISPLAY ONLY: the server independently
// derives the real destination from the detected source language and
// enforces PROTECTED_SUFFIXES regardless of anything computed here.
const ENGLISH_SUBTITLE_SUFFIXES = [".en.srt", ".en.hi.srt", ".en.forced.srt", ".en.sdh.srt"];

function computeDestination(videoPath: string): string {
  // Mirrors output.resolve_output_path()'s naming exactly: <video stem>.en.srt
  // beside the video. This is DISPLAY ONLY -- the server independently
  // computes and enforces the real value; nothing here is ever sent as input.
  const parts = videoPath.split("/");
  const file = parts.pop() ?? "";
  const stem = file.includes(".") ? file.slice(0, file.lastIndexOf(".")) : file;
  const dir = parts.length > 0 ? parts.join("/") + "/" : "";
  return `${dir}${stem}.en.srt`;
}

// Why a video can't go into the batch queue, or null if it can. The batch
// only ever auto-picks a source when there is exactly one original-language
// subtitle beside the video and no English one yet -- several candidates is
// genuinely ambiguous (which language is the dialogue?), and guessing wrong
// means a wrong-language translation committed to the library.
function batchIneligibleReason(entry: BrowseEntry): string | null {
  if (entry.has_english_subtitle) return "English subtitle already exists";
  const sources = entry.source_subtitles ?? [];
  if (sources.length === 0) return "No original-language subtitle beside this video";
  if (sources.length > 1) return "Several original-language subtitles -- queue this one manually";
  return null;
}

export function TranslateSrtPage() {
  const navigate = useNavigate();
  const { notify } = useToast();

  // Batch queueing (the subtitle-translation counterpart of LibraryPage's
  // batch mode). currentBrowsePath mirrors the episode browser's own
  // navigation via its onPathChange prop, so this panel reads the SAME
  // cached listing (identical useBrowse key) instead of fetching twice.
  const [batchMode, setBatchMode] = useState(false);
  const [currentBrowsePath, setCurrentBrowsePath] = useState("");
  const [batchSelected, setBatchSelected] = useState<Set<string>>(new Set());
  const [batchMessage, setBatchMessage] = useState<{ text: string; kind: "ok" | "error" } | null>(null);
  const [batchQueueing, setBatchQueueing] = useState(false);
  // Every video entry seen so far, by path: a selection can span folders,
  // and the source subtitle for each queued video must come from ITS OWN
  // listing, not whichever folder is currently open.
  const seenEntries = useRef(new Map<string, BrowseEntry>());
  const [videoPath, setVideoPath] = useState<string | null>(null);
  const [source, setSource] = useState<SourceSelection>(null);
  const [sourceTab, setSourceTab] = useState<"library" | "upload">("library");
  const [sourceLang, setSourceLang] = useState("auto");
  const [overwriteOriginal, setOverwriteOriginal] = useState(false);
  const [overwriteEnglish, setOverwriteEnglish] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const languages = useLanguages();
  const media = useMedia(videoPath);
  const uploadSrt = useUploadSrt();
  const createJob = useCreateSrtTranslationJob();
  const dirListing = useBrowse(currentBrowsePath, "video");

  const videoEntries = dirListing.data?.entries.filter((e) => e.type === "video") ?? [];
  useEffect(() => {
    for (const e of dirListing.data?.entries ?? []) {
      if (e.type === "video") seenEntries.current.set(e.path, e);
    }
  }, [dirListing.data]);
  const readyPaths = videoEntries.filter((e) => batchIneligibleReason(e) === null).map((e) => e.path);
  const alreadyDone = videoEntries.filter((e) => e.has_english_subtitle).length;
  const noSource = videoEntries.filter((e) => !e.has_english_subtitle && (e.source_subtitles ?? []).length === 0)
    .length;
  const ambiguous = videoEntries.filter((e) => !e.has_english_subtitle && (e.source_subtitles ?? []).length > 1)
    .length;

  const toggleBatchSelect = (path: string) => {
    setBatchSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const queueBatch = async () => {
    // Re-derive eligibility from the remembered entry at submit time (the
    // listing may have refreshed since the box was ticked) and take the
    // source from that entry -- never from the folder currently on screen.
    const jobs = Array.from(batchSelected)
      .map((path) => seenEntries.current.get(path))
      .filter((e): e is BrowseEntry => e !== undefined && batchIneligibleReason(e) === null);
    if (jobs.length === 0) return;
    setBatchQueueing(true);
    setBatchMessage(null);
    const results = await Promise.allSettled(
      jobs.map((e) =>
        createJob.mutateAsync({
          video_path: e.path,
          source_srt_path: (e.source_subtitles as string[])[0],
          source_lang: sourceLang,
          overwrite_original: false,
          overwrite_english: false,
        }),
      ),
    );
    setBatchQueueing(false);
    const succeeded = results.filter((r) => r.status === "fulfilled").length;
    const failed = results.length - succeeded;
    const firstFailure = results.find((r): r is PromiseRejectedResult => r.status === "rejected");
    const why =
      firstFailure && firstFailure.reason instanceof ApiError ? ` (${firstFailure.reason.message})` : "";
    setBatchMessage(
      failed === 0
        ? { text: `Queued ${succeeded} translation job${succeeded === 1 ? "" : "s"}.`, kind: "ok" }
        : { text: `Queued ${succeeded}, ${failed} failed to queue${why}.`, kind: "error" },
    );
    // Only clear what actually queued -- a failed one stays ticked so it
    // is visible and retryable, never silently dropped.
    setBatchSelected((prev) => {
      const next = new Set(prev);
      jobs.forEach((e, i) => {
        if (results[i].status === "fulfilled") next.delete(e.path);
      });
      return next;
    });
  };

  const selectVideo = (path: string) => {
    setVideoPath(path);
    setSource(null);
    setOverwriteOriginal(false);
    setOverwriteEnglish(false);
  };

  const selectLibrarySource = (path: string) => {
    setSource({ mode: "library", path, filename: path.split("/").pop() ?? path });
  };

  const handleFileChosen = (file: File | undefined) => {
    if (!file) return;
    uploadSrt.mutate(file, {
      onSuccess: ({ upload_id, filename }) => {
        setSource({ mode: "upload", uploadId: upload_id, filename });
        notify(`Uploaded ${filename}.`);
      },
      onError: (err) => notify(err instanceof ApiError ? err.message : "Upload failed", "error"),
    });
  };

  const existingSubtitles = media.data?.existing_subtitles ?? [];
  const existingEnglish = existingSubtitles.some((p) => p.endsWith(".en.srt"));
  const existingOriginal = existingSubtitles.some(
    (p) => !ENGLISH_SUBTITLE_SUFFIXES.some((suffix) => p.endsWith(suffix)),
  );
  const destinationPreview = videoPath ? computeDestination(videoPath) : "";

  const submit = () => {
    if (!videoPath || !source) return;
    createJob.mutate(
      {
        video_path: videoPath,
        ...(source.mode === "library"
          ? { source_srt_path: source.path }
          : { source_upload_id: source.uploadId }),
        source_lang: sourceLang,
        overwrite_original: overwriteOriginal,
        overwrite_english: overwriteEnglish,
      },
      {
        onSuccess: ({ job }) => {
          notify("SRT translation job queued.");
          navigate(`/jobs/${job.id}`);
        },
        onError: (err) => notify(err instanceof ApiError ? err.message : "Failed to queue job", "error"),
      },
    );
  };

  return (
    <section className="workspace">
      <div className="browser-column">
        <MediaBrowser
          selectedPath={videoPath}
          onSelect={selectVideo}
          fileType="video"
          title="Associated episode (required)"
          onPathChange={setCurrentBrowsePath}
          batchSelectable={batchMode}
          batchSelected={batchSelected}
          onToggleBatchSelect={toggleBatchSelect}
          batchDisabledReason={batchIneligibleReason}
        />
        <div className="panel batch-panel">
          <div className="panel-head">
            <h2>Batch translate</h2>
            <button className="text-button" onClick={() => setBatchMode((v) => !v)}>
              {batchMode ? "Done" : "Batch translate…"}
            </button>
          </div>
          {batchMode && (
            <>
              <div className="selection-empty">
                Translates each episode's existing original-language subtitle (the
                <code> .srt </code>beside the video) to English. Only episodes with exactly one
                such subtitle and no English one yet can be queued; existing files are kept,
                never replaced.
              </div>
              <div className="option-row">
                <span>
                  {videoEntries.length} video{videoEntries.length === 1 ? "" : "s"} here:{" "}
                  {readyPaths.length} ready to translate
                </span>
              </div>
              {(alreadyDone > 0 || noSource > 0 || ambiguous > 0) && (
                <div className="lang-info">
                  Skipped: {alreadyDone} already have English, {noSource} have no source subtitle,{" "}
                  {ambiguous} have several source subtitles.
                </div>
              )}
              <label className="existing-row">
                Source language
                <select value={sourceLang} onChange={(e) => setSourceLang(e.target.value)}>
                  <option value="auto">Auto Detect</option>
                  {(languages.data?.languages ?? []).map((code) => (
                    <option key={code} value={code}>
                      {code}
                    </option>
                  ))}
                </select>
              </label>
              <div className="batch-actions">
                <button
                  className="text-button"
                  onClick={() => setBatchSelected(new Set(readyPaths))}
                  disabled={readyPaths.length === 0}
                >
                  Select all ready
                </button>
                <button
                  className="text-button"
                  onClick={() => setBatchSelected(new Set())}
                  disabled={batchSelected.size === 0}
                >
                  Clear selection
                </button>
              </div>
              <button
                className="primary"
                onClick={queueBatch}
                disabled={batchSelected.size === 0 || batchQueueing}
              >
                {batchQueueing ? "Queueing…" : `Translate ${batchSelected.size} selected`}
              </button>
              {batchMessage && <div className={`message ${batchMessage.kind}`}>{batchMessage.text}</div>}
            </>
          )}
        </div>
      </div>
      <div className="panel selection-panel">
        <div className="panel-head">
          <h2>Translate existing subtitle</h2>
        </div>
        {!videoPath && (
          <div className="selection-empty">
            Select the episode this subtitle belongs to. The translation is written
            to that episode's own file path, and the episode's series drives glossary
            accuracy (character/place-name protection).
          </div>
        )}
        {videoPath && (
          <>
            <div className="summary">
              <strong>{videoPath.split("/").pop()}</strong>
              <div className="lang-info">Will write to: {destinationPreview}</div>
            </div>

            {existingEnglish && (
              <label className="existing-row">
                English subtitle already exists
                <select
                  value={overwriteEnglish ? "replace" : "keep"}
                  onChange={(e) => setOverwriteEnglish(e.target.value === "replace")}
                >
                  <option value="keep">Keep</option>
                  <option value="replace">Replace</option>
                </select>
              </label>
            )}

            <div className="panel-head">
              <h2>Original-language subtitle</h2>
            </div>
            {existingOriginal && (
              <label className="existing-row">
                Original-language subtitle already exists
                <select
                  value={overwriteOriginal ? "replace" : "keep"}
                  onChange={(e) => setOverwriteOriginal(e.target.value === "replace")}
                >
                  <option value="keep">Keep</option>
                  <option value="replace">Replace</option>
                </select>
              </label>
            )}
            <div className="selection-empty">
              The source you pick below is also committed to the library as this
              episode's own original-language subtitle (alongside the English
              translation), same as a transcription job's output.
            </div>
            <div className="nav" role="tablist">
              <button
                className={sourceTab === "library" ? "active" : ""}
                onClick={() => setSourceTab("library")}
              >
                Browse library
              </button>
              <button
                className={sourceTab === "upload" ? "active" : ""}
                onClick={() => setSourceTab("upload")}
              >
                Upload from this computer
              </button>
            </div>

            {sourceTab === "library" && (
              <MediaBrowser
                selectedPath={source?.mode === "library" ? source.path : null}
                onSelect={selectLibrarySource}
                fileType="srt"
                title="Original-language subtitles"
              />
            )}
            {sourceTab === "upload" && (
              <div className="option-row">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".srt"
                  onChange={(e) => handleFileChosen(e.target.files?.[0])}
                  disabled={uploadSrt.isPending}
                />
                {uploadSrt.isPending && <span>Uploading&hellip;</span>}
              </div>
            )}
            {source && (
              <div className="summary">
                <strong>{source.filename}</strong>
                {source.mode === "upload" && " (uploaded)"}
              </div>
            )}

            <label className="existing-row">
              Source language
              <select value={sourceLang} onChange={(e) => setSourceLang(e.target.value)}>
                <option value="auto">Auto Detect</option>
                {(languages.data?.languages ?? []).map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </select>
            </label>

            <div className="option-row">
              <span>Target language</span>
              <strong>English</strong>
            </div>

            <button
              className="primary"
              onClick={submit}
              disabled={createJob.isPending || !source}
            >
              {createJob.isPending ? "Starting…" : "Translate"}
            </button>
          </>
        )}
      </div>
    </section>
  );
}
