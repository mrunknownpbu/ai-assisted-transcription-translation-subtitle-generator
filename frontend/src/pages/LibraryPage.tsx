import { useState } from "react";

import { useCreateJob, useMedia } from "../api/hooks";
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

  const media = useMedia(selectedPath);
  const createJob = useCreateJob();

  const select = (path: string) => {
    setSelectedPath(path);
    setAudioStreamIndex(null);
    setOverwriteOriginal(false);
    setOverwriteEnglish(false);
    setMessage(null);
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
      <MediaBrowser selectedPath={selectedPath} onSelect={select} />
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
