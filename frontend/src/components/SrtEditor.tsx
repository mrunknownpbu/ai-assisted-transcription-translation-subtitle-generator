import { useEffect, useState } from "react";

import { useJobSrt, useUpdateJobSrt } from "../api/hooks";
import { ApiError } from "../api/client";
import { useToast } from "./Toast";

function fmtCueTime(seconds: number): string {
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(1);
  return `${m}:${sec.padStart(4, "0")}`;
}

interface Props {
  jobId: string;
}

// IMPROVEMENT_PLAN.md 4.2: inline subtitle review/fix editor. Closed by
// default -- useJobSrt()'s `enabled` flag means opening this is the only
// thing that triggers the extra fetch, so an ordinary job-detail view
// (the overwhelming majority of visits) costs nothing extra. Text-only:
// timing/cue-count are never sent back to the server (api.py's PUT
// re-reads the file fresh and only overwrites `lines` by index -- see
// its own docstring), so this can never desync a cue's timing from its
// text or insert/remove a cue.
export function SrtEditor({ jobId }: Props) {
  const [open, setOpen] = useState(false);
  const { data, isLoading, error } = useJobSrt(jobId, open);
  const updateSrt = useUpdateJobSrt(jobId);
  const { notify } = useToast();
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [original, setOriginal] = useState<Record<number, string>>({});

  useEffect(() => {
    // Reset local drafts whenever fresh server data arrives (first open,
    // or right after a successful save) -- never overwrites an in-flight
    // unsaved edit, since this only re-runs when `data` itself changes.
    if (!data) return;
    const next: Record<number, string> = {};
    for (const cue of data.cues) next[cue.index] = cue.lines.join("\n");
    setDrafts(next);
    setOriginal(next);
  }, [data]);

  if (!open) {
    return (
      <div className="detail">
        <button className="text-button" onClick={() => setOpen(true)}>
          Edit subtitles
        </button>
      </div>
    );
  }

  const dirtyIndices = Object.keys(drafts)
    .map(Number)
    .filter((i) => drafts[i] !== original[i]);

  const save = () => {
    const edits = dirtyIndices.map((index) => ({ index, lines: drafts[index].split("\n") }));
    if (edits.length === 0) return;
    updateSrt.mutate(
      { edits },
      {
        onSuccess: () => notify(`Saved ${edits.length} cue${edits.length === 1 ? "" : "s"}.`),
        onError: (err) => notify(err instanceof ApiError ? err.message : "Failed to save", "error"),
      },
    );
  };

  const flagged = new Set(data?.flagged_indices ?? []);
  // Flagged cues first, then in cue order -- the real task is "review
  // the flagged ones", not "read the whole episode top to bottom".
  const orderedCues = data
    ? [...data.cues].sort((a, b) => {
        const af = flagged.has(a.index) ? 0 : 1;
        const bf = flagged.has(b.index) ? 0 : 1;
        return af - bf || a.index - b.index;
      })
    : [];

  return (
    <div className="detail srt-editor">
      <div className="panel-head-sub">
        <h3>Edit subtitles{flagged.size > 0 ? ` (${flagged.size} flagged)` : ""}</h3>
        <button className="text-button" onClick={() => setOpen(false)}>
          Close
        </button>
      </div>
      {isLoading && <div>Loading&hellip;</div>}
      {error && (
        <div className="message error">{error instanceof ApiError ? error.message : "Failed to load"}</div>
      )}
      {data && (
        <>
          <div className="srt-editor-list">
            {orderedCues.map((cue) => (
              <div key={cue.index} className={`srt-editor-row${flagged.has(cue.index) ? " flagged" : ""}`}>
                <div className="srt-editor-meta">
                  <span>#{cue.index + 1}</span>
                  <span className="srt-editor-time">
                    {fmtCueTime(cue.start)} → {fmtCueTime(cue.end)}
                  </span>
                  {flagged.has(cue.index) && (
                    <span className="srt-editor-flag" title="Flagged by QC">
                      ⚠
                    </span>
                  )}
                </div>
                <textarea
                  className="srt-editor-text"
                  rows={2}
                  value={drafts[cue.index] ?? ""}
                  onChange={(e) => setDrafts((prev) => ({ ...prev, [cue.index]: e.target.value }))}
                />
              </div>
            ))}
          </div>
          <button className="primary" onClick={save} disabled={dirtyIndices.length === 0 || updateSrt.isPending}>
            {updateSrt.isPending
              ? "Saving…"
              : `Save ${dirtyIndices.length} change${dirtyIndices.length === 1 ? "" : "s"}`}
          </button>
        </>
      )}
    </div>
  );
}
