import { useEffect, useState } from "react";

import { useBrowse } from "../api/hooks";
import type { BrowseEntry } from "../api/types";
import { fmtBytes } from "../format";

interface Props {
  selectedPath: string | null;
  onSelect: (path: string) => void;
  fileType?: "video" | "srt";
  title?: string;
  // IMPROVEMENT_PLAN.md 4.1 -- all optional, so the existing single-select
  // callers (TranslateSrtPage's two uses) are completely unaffected.
  // onPathChange lets a parent (LibraryPage) track the currently-browsed
  // directory to drive its own batch-queue panel, sharing this same
  // useBrowse() query/cache rather than fetching the listing twice.
  onPathChange?: (path: string) => void;
  batchSelectable?: boolean;
  batchSelected?: Set<string>;
  onToggleBatchSelect?: (path: string) => void;
  // Returns a human reason a video can't be batch-queued (its checkbox is
  // then disabled, with the reason as a tooltip), or null if it can. Lets a
  // page keep ineligible rows out of the selection instead of silently
  // dropping them at submit time. Omitted = every video is selectable.
  batchDisabledReason?: (entry: BrowseEntry) => string | null;
}

export function MediaBrowser({
  selectedPath,
  onSelect,
  fileType = "video",
  title = "Media library",
  onPathChange,
  batchSelectable = false,
  batchSelected,
  onToggleBatchSelect,
  batchDisabledReason,
}: Props) {
  const [path, setPath] = useState("");
  const { data, isLoading, error } = useBrowse(path, fileType);

  useEffect(() => {
    onPathChange?.(path);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  const crumbs = path ? path.split("/") : [];

  return (
    <div className="panel browser-panel">
      <div className="panel-head">
        <h2>{title}</h2>
        <button
          className="icon-button"
          title="Go to parent folder"
          aria-label="Go to parent folder"
          onClick={() => setPath(crumbs.slice(0, -1).join("/"))}
          disabled={!path}
        >
          &#8593;
        </button>
      </div>
      <div className="crumbs">
        <button className="crumb" onClick={() => setPath("")}>
          Library
        </button>
        {crumbs.map((part, i) => (
          <span key={i}>
            {" / "}
            <button className="crumb" onClick={() => setPath(crumbs.slice(0, i + 1).join("/"))}>
              {part}
            </button>
          </span>
        ))}
      </div>
      <div className="browser" aria-live="polite">
        {isLoading && "Loading…"}
        {error && `Failed to load: ${(error as Error).message}`}
        {data?.entries.length === 0 && <div className="browser-row">Empty folder</div>}
        {data?.entries.map((entry) =>
          entry.type === "directory" ? (
            <button key={entry.path} className="browser-row" onClick={() => setPath(entry.path)}>
              &#128193; {entry.name}
            </button>
          ) : (
            <div key={entry.path} className="browser-row-wrap">
              {batchSelectable && entry.type === "video" && (
                <input
                  type="checkbox"
                  className="browser-row-checkbox"
                  aria-label={`Select ${entry.name} for batch queueing`}
                  checked={batchSelected?.has(entry.path) ?? false}
                  disabled={batchDisabledReason?.(entry) != null}
                  title={batchDisabledReason?.(entry) ?? undefined}
                  onChange={() => onToggleBatchSelect?.(entry.path)}
                />
              )}
              <button
                className={`browser-row${entry.path === selectedPath ? " selected" : ""}`}
                title={fmtBytes(entry.size)}
                onClick={() => onSelect(entry.path)}
              >
                {entry.type === "srt" ? "\u{1F4C4}" : "\u{1F3AC}"} {entry.name}
                {entry.type === "video" && entry.has_english_subtitle && (
                  <span className="browser-row-done" title="English subtitle already exists">
                    {" "}
                    ✓
                  </span>
                )}
              </button>
            </div>
          ),
        )}
      </div>
    </div>
  );
}
