import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { useCreateSrtTranslationJob, useLanguages, useMedia, useUploadSrt } from "../api/hooks";
import { MediaBrowser } from "../components/MediaBrowser";
import { useToast } from "../components/Toast";

type SourceSelection =
  | { mode: "library"; path: string; filename: string }
  | { mode: "upload"; uploadId: string; filename: string }
  | null;

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

export function TranslateSrtPage() {
  const navigate = useNavigate();
  const { notify } = useToast();
  const [videoPath, setVideoPath] = useState<string | null>(null);
  const [source, setSource] = useState<SourceSelection>(null);
  const [sourceTab, setSourceTab] = useState<"library" | "upload">("library");
  const [sourceLang, setSourceLang] = useState("auto");
  const [overwriteEnglish, setOverwriteEnglish] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const languages = useLanguages();
  const media = useMedia(videoPath);
  const uploadSrt = useUploadSrt();
  const createJob = useCreateSrtTranslationJob();

  const selectVideo = (path: string) => {
    setVideoPath(path);
    setSource(null);
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

  const existingEnglish = (media.data?.existing_subtitles ?? []).some((p) => p.endsWith(".en.srt"));
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
      <MediaBrowser
        selectedPath={videoPath}
        onSelect={selectVideo}
        fileType="video"
        title="Associated episode (required)"
      />
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
