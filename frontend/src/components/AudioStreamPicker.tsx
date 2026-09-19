import { useAudioStreams } from "../api/hooks";
import type { AudioStream, MediaMetadata } from "../api/types";
import { fmtPercent } from "../format";

interface Props {
  path: string;
  media: MediaMetadata;
  selectedIndex: number | null;
  onSelectIndex: (index: number | null) => void;
}

function flagList(s: AudioStream): string[] {
  const flags: string[] = [];
  if (s.default) flags.push("Default");
  if (s.commentary) flags.push("Commentary");
  if (s.visual_impaired) flags.push("Audio description");
  if (s.hearing_impaired) flags.push("Hearing-impaired");
  return flags;
}

export function AudioStreamPicker({ path, media, selectedIndex, onSelectIndex }: Props) {
  const { data, isFetching, refetch } = useAudioStreams(path);
  const streams = data?.streams ?? media.audio_tracks;
  const recommendedIndex = data?.recommended_index ?? null;

  return (
    <div className="audio-streams">
      <div className="panel-head-sub">
        <h3>Audio Streams</h3>
        <button className="text-button" onClick={() => refetch()} disabled={isFetching}>
          {isFetching ? "Analyzing…" : "Analyze (detect language)"}
        </button>
      </div>
      <div className="stream-list">
        {streams.map((s) => {
          const alt = data?.alternates.find((a) => a.index === s.index);
          const isRecommended = s.index === recommendedIndex;
          return (
            <div key={s.index} className={`stream-card${isRecommended ? " recommended" : ""}`}>
              <div className="stream-card-title">
                {isRecommended && "★ "}
                Stream {s.index}
                {s.title ? ` — ${s.title}` : ""}
                {s.language ? ` (${s.language})` : ""}
              </div>
              <div className="lang-info">
                {s.codec ?? "?"} &middot; {s.channels ?? "?"}ch {s.channel_layout ?? ""}
                {alt && (
                  <>
                    {" · detected "}
                    {alt.language ?? "?"} ({fmtPercent(alt.confidence)})
                  </>
                )}
              </div>
              {flagList(s).length > 0 && <div className="lang-info">{flagList(s).join(", ")}</div>}
            </div>
          );
        })}
      </div>
      {data && (
        <div className="recommendation">
          {data.reason}
          {data.alternates.length > 0 &&
            ` — alternates: ${data.alternates.map((a) => `#${a.index} (${a.language ?? "?"})`).join(", ")}`}
        </div>
      )}
      <label style={{ display: "block", marginTop: 10, fontSize: 13 }}>
        Selected audio stream{" "}
        <select
          value={selectedIndex ?? ""}
          onChange={(e) => onSelectIndex(e.target.value === "" ? null : Number(e.target.value))}
        >
          <option value="">Auto (recommended)</option>
          {streams.map((s) => (
            <option key={s.index} value={s.index}>
              Stream {s.index}
              {s.title ? ` — ${s.title}` : ""}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
