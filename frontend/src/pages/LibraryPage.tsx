import { useState } from "react";

import { useBrowse, useCreateJob, useMedia } from "../api/hooks";
import { ApiError } from "../api/client";
import { AudioStreamPicker } from "../components/AudioStreamPicker";
import { MediaBrowser } from "../components/MediaBrowser";
import { fmtDuration } from "../format";

export function LibraryPage() {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [audioStreamIndex, setAudioStreamIndex] = useState<number | null>(null);
  const [overwriteOriginal, setOverwriteOriginal] = useState(false);
  const [overwriteEnglish, setOverwriteEnglish] = useState(false);
  const [message, setMessage] = useState<{ text: string; kind: "ok" | "error" } | null>(null);

  // IMPROVEMENT_PLAN.md 4.1: batch queueing. currentBrowsePath mirrors
  // MediaBrowser's own internal navigation (via its onPathChange prop) so
  // this panel can query the SAME directory listing -- useBrowse() with an
  // identical (path, fileType) key shares React Query's cache with
  // MediaBrowser's own fetch rather than doubling the request.
  const [batchMode, setBatchMode] = useState(false);
  const [currentBrowsePath, setCurrentBrowsePath] = useState("");
  const [batchSelected, setBatchSelected] = useState<Set<string>>(new Set());
  const [batchMessage, setBatchMessage] = useState<{ text: string; kind: "ok" | "error" } | null>(null);
  const [batchQueueing, setBatchQueueing] = useState(false);

  const media = useMedia(selectedPath);
  const createJob = useCreateJob();
  const dirListing = useBrowse(currentBrowsePath, "video");

  const select = (path: string) => {
    setSelectedPath(path);
    setAudioStreamIndex(null);
    setOverwriteOriginal(false);
    setOverwriteEnglish(false);
    setMessage(null);
  };

  const videoEntries = dirListing.data?.entries.filter((e) => e.type === "video") ?? [];
  const untranscribedPaths = videoEntries.filter((e) => !e.has_english_subtitle).map((e) => e.path);

  const toggleBatchSelect = (path: string) => {
    setBatchSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const selectAllUntranscribed = () => {
    setBatchSelected(new Set(untranscribedPaths));
  };

  const clearBatchSelection = () => setBatchSelected(new Set());

  const queueBatch = async () => {
    const paths = Array.from(batchSelected);
    if (paths.length === 0) return;
    setBatchQueueing(true);
    setBatchMessage(null);
    const results = await Promise.allSettled(
      paths.map((video_path) =>
        createJob.mutateAsync({
          video_path,
          audio_stream_index: null,
          overwrite_original: false,
          overwrite_english: false,
        }),
      ),
    );
    setBatchQueueing(false);
    const succeeded = results.filter((r) => r.status === "fulfilled").length;
    const failed = results.length - succeeded;
    setBatchMessage(
      failed === 0
        ? { text: `Queued ${succeeded} job${succeeded === 1 ? "" : "s"}.`, kind: "ok" }
        : { text: `Queued ${succeeded}, ${failed} failed to queue.`, kind: "error" },
    );
    // Only drop the ones that actually queued -- a failed one stays
    // checked so the operator can see and retry it, not silently lose it.
    setBatchSelected((prev) => {
      const next = new Set(prev);
      paths.forEach((p, i) => {
        if (results[i].status === "fulfilled") next.delete(p);
      });
      return next;
    });
  };

  const existingOriginal = media.data?.existing_subtitles.filter((p) => !p.endsWith(".en.srt")) ?? [];
  const existingEnglish = media.data?.existing_subtitles.filter((p) => p.endsWith(".en.srt")) ?? [];

  const start = () => {
    if (!selectedPath) return;
    setMessage(null);
    createJob.mutate(
      {
        video_path: selectedPath,
        audio_stream_index: audioStreamIndex,
        overwrite_original: overwriteOriginal,
        overwrite_english: overwriteEnglish,
      },
      {
        onSuccess: () => setMessage({ text: "Job queued.", kind: "ok" }),
        onError: (err) =>
          setMessage({ text: err instanceof ApiError ? err.message : "Failed to queue job", kind: "error" }),
      },
    );
  };

  return (
    <section className="workspace">
      <div className="browser-column">
        <MediaBrowser
          selectedPath={selectedPath}
          onSelect={select}
          onPathChange={setCurrentBrowsePath}
          batchSelectable={batchMode}
          batchSelected={batchSelected}
          onToggleBatchSelect={toggleBatchSelect}
        />
        <div className="panel batch-panel">
          <div className="panel-head">
            <h2>Batch queue</h2>
            <button className="text-button" onClick={() => setBatchMode((v) => !v)}>
              {batchMode ? "Done" : "Batch queue…"}
            </button>
          </div>
          {batchMode && (
            <>
              <div className="option-row">
                <span>
                  {videoEntries.length} video{videoEntries.length === 1 ? "" : "s"} here,{" "}
                  {untranscribedPaths.length} untranscribed
                </span>
              </div>
              <div className="batch-actions">
                <button
                  className="text-button"
                  onClick={selectAllUntranscribed}
                  disabled={untranscribedPaths.length === 0}
                >
                  Select all untranscribed
                </button>
                <button className="text-button" onClick={clearBatchSelection} disabled={batchSelected.size === 0}>
                  Clear selection
                </button>
              </div>
              <button
                className="primary"
                onClick={queueBatch}
                disabled={batchSelected.size === 0 || batchQueueing}
              >
                {batchQueueing ? "Queueing…" : `Queue ${batchSelected.size} selected`}
              </button>
              {batchMessage && <div className={`message ${batchMessage.kind}`}>{batchMessage.text}</div>}
            </>
          )}
        </div>
      </div>
      <div className="panel selection-panel">
        <div className="panel-head">
          <h2>Selection</h2>
          <button className="text-button" onClick={() => select("")} disabled={!selectedPath}>
            Clear
          </button>
        </div>
        {!selectedPath && <div className="selection-empty">Select a video from the library.</div>}
        {selectedPath && media.isLoading && <div>Loading&hellip;</div>}
        {selectedPath && media.data && (
          <>
            <div className="summary">
              <strong>{media.data.filename}</strong>
              <div className="lang-info">
                {fmtDuration(media.data.duration)} &middot; {media.data.audio_tracks.length} audio track
                {media.data.audio_tracks.length === 1 ? "" : "s"}
              </div>
            </div>
            {(existingOriginal.length > 0 || existingEnglish.length > 0) && (
              <div className="existing">
                {existingOriginal.length > 0 && (
                  <label className="existing-row">
                    Original-language subtitle exists{" "}
                    <select
                      value={overwriteOriginal ? "replace" : "keep"}
                      onChange={(e) => setOverwriteOriginal(e.target.value === "replace")}
                    >
                      <option value="keep">Keep</option>
                      <option value="replace">Replace</option>
                    </select>
                  </label>
                )}
                {existingEnglish.length > 0 && (
                  <label className="existing-row">
                    English subtitle exists{" "}
                    <select
                      value={overwriteEnglish ? "replace" : "keep"}
                      onChange={(e) => setOverwriteEnglish(e.target.value === "replace")}
                    >
                      <option value="keep">Keep</option>
                      <option value="replace">Replace</option>
                    </select>
                  </label>
                )}
              </div>
            )}
            <AudioStreamPicker
              path={selectedPath}
              media={media.data}
              selectedIndex={audioStreamIndex}
              onSelectIndex={setAudioStreamIndex}
            />
            <div className="options">
              <div className="option-row">
                <span>Transcription</span>
                <strong>faster-whisper large-v3</strong>
              </div>
              <div className="option-row">
                <span>Translation</span>
                <strong>NLLB-200 distilled 1.3B</strong>
              </div>
            </div>
            <button className="primary" onClick={start} disabled={createJob.isPending}>
              {createJob.isPending ? "Starting…" : "Start processing"}
            </button>
            {message && <div className={`message ${message.kind}`}>{message.text}</div>}
          </>
        )}
      </div>
    </section>
  );
}
