import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { useCreateRetimeJob, useUploadSrt } from "../api/hooks";
import { MediaBrowser } from "../components/MediaBrowser";
import { useToast } from "../components/Toast";

type SourceSelection =
  | { mode: "library"; path: string; filename: string }
  | { mode: "upload"; uploadId: string; filename: string }
  | null;

// `film.tr.srt` -> "tr". Mirrors retime_job.language_from_filename; used only
// to pre-fill the field, the server reads the same tag when none is sent.
export function languageFromFilename(name: string): string {
  const match = /\.([a-z]{2,3})(?:\.retimed)?\.srt$/.exec(name);
  return match ? match[1] : "";
}

// DISPLAY ONLY: the server derives and enforces the real destination.
export function retimeDestination(videoPath: string, language: string, replace: boolean): string {
  const parts = videoPath.split("/");
  const file = parts.pop() ?? "";
  const stem = file.includes(".") ? file.slice(0, file.lastIndexOf(".")) : file;
  const dir = parts.length > 0 ? parts.join("/") + "/" : "";
  return `${dir}${stem}.${language || "xx"}${replace ? "" : ".retimed"}.srt`;
}

export function RetimePage() {
  const navigate = useNavigate();
  const { notify } = useToast();
  const [videoPath, setVideoPath] = useState<string | null>(null);
  const [source, setSource] = useState<SourceSelection>(null);
  const [sourceTab, setSourceTab] = useState<"library" | "upload">("library");
  const [language, setLanguage] = useState("");
  const [replaceOriginal, setReplaceOriginal] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadSrt = useUploadSrt();
  const createJob = useCreateRetimeJob();

  const languageValid = /^[a-z]{2,3}$/.test(language);

  const selectLibrarySource = (path: string) => {
    const filename = path.split("/").pop() ?? path;
    setSource({ mode: "library", path, filename });
    const tag = languageFromFilename(filename);
    if (tag) setLanguage(tag);
  };

  const handleFileChosen = (file: File | undefined) => {
    if (!file) return;
    uploadSrt.mutate(file, {
      onSuccess: ({ upload_id, filename }) => {
        setSource({ mode: "upload", uploadId: upload_id, filename });
        const tag = languageFromFilename(filename);
        if (tag) setLanguage(tag);
        notify(`Uploaded ${filename}.`);
      },
      onError: (err) => notify(err instanceof ApiError ? err.message : "Upload failed", "error"),
    });
  };

  const submit = () => {
    if (!videoPath || !source || !languageValid) return;
    createJob.mutate(
      {
        video_path: videoPath,
        ...(source.mode === "library" ? { source_srt_path: source.path } : { source_upload_id: source.uploadId }),
        language,
        replace_original: replaceOriginal,
      },
      {
        onSuccess: ({ job }) => {
          notify("Re-timing job queued.");
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
          onSelect={(path) => setVideoPath(path)}
          fileType="video"
          title="Video (required)"
        />
      </div>
      <div className="panel selection-panel">
        <div className="panel-head">
          <h2>Re-time existing subtitle</h2>
        </div>
        {!videoPath && (
          <div className="selection-empty">
            Select the video the subtitle belongs to. Its audio is compared with the subtitle's
            words and each cue is moved to where it is spoken. The text is never changed. Fixes a
            constant shift, a frame-rate mismatch, or a different cut.
          </div>
        )}
        {videoPath && (
          <>
            <div className="summary">
              <strong>{videoPath.split("/").pop()}</strong>
            </div>

            <div className="nav" role="tablist">
              <button className={sourceTab === "library" ? "active" : ""} onClick={() => setSourceTab("library")}>
                Browse library
              </button>
              <button className={sourceTab === "upload" ? "active" : ""} onClick={() => setSourceTab("upload")}>
                Upload from this computer
              </button>
            </div>
            {sourceTab === "library" && (
              <MediaBrowser
                selectedPath={source?.mode === "library" ? source.path : null}
                onSelect={selectLibrarySource}
                fileType="srt"
                title="Subtitle to re-time"
              />
            )}
            {sourceTab === "upload" && (
              <div className="option-row">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".srt,.vtt"
                  aria-label="Subtitle file to upload"
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
              Subtitle language
              <input
                aria-label="Subtitle language"
                value={language}
                maxLength={3}
                placeholder="tr"
                onChange={(e) => setLanguage(e.target.value.toLowerCase())}
              />
            </label>
            {language !== "" && !languageValid && (
              <div className="lang-info">Use the 2-3 letter code of the subtitle's own language.</div>
            )}

            <label className="existing-row">
              <input
                type="checkbox"
                checked={replaceOriginal}
                onChange={(e) => setReplaceOriginal(e.target.checked)}
              />
              Replace the library subtitle instead of keeping it
            </label>
            <div className="lang-info">Will write to: {retimeDestination(videoPath, language, replaceOriginal)}</div>
            {replaceOriginal && (
              <div className="lang-info">
                The episode's current <code>.{language || "xx"}.srt</code> is overwritten. Edits made to it are
                lost.
              </div>
            )}
            <div className="selection-empty">
              The video's audio is transcribed first unless it already has a transcript, which can take
              several minutes. A subtitle that does not match the audio is refused and nothing is written.
            </div>

            <button className="primary" onClick={submit} disabled={createJob.isPending || !source || !languageValid}>
              {createJob.isPending ? "Starting…" : "Re-time"}
            </button>
          </>
        )}
      </div>
    </section>
  );
}
