import { useState } from "react";

import { useBrowse } from "../api/hooks";
import { fmtBytes } from "../format";

interface Props {
  selectedPath: string | null;
  onSelect: (path: string) => void;
  fileType?: "video" | "srt";
  title?: string;
}

export function MediaBrowser({ selectedPath, onSelect, fileType = "video", title = "Media library" }: Props) {
  const [path, setPath] = useState("");
  const { data, isLoading, error } = useBrowse(path, fileType);

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
            <button
              key={entry.path}
              className={`browser-row${entry.path === selectedPath ? " selected" : ""}`}
              title={fmtBytes(entry.size)}
              onClick={() => onSelect(entry.path)}
            >
              {entry.type === "srt" ? "\u{1F4C4}" : "\u{1F3AC}"} {entry.name}
            </button>
          ),
        )}
      </div>
    </div>
  );
}
