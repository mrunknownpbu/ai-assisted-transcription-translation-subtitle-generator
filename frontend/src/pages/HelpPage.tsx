const FAQ = [
  {
    q: "Why does it use the audio instead of existing subtitles?",
    a: "Existing subtitle files (embedded or sidecar) are frequently mistimed, incomplete, or in the wrong language for what's actually spoken. This platform always transcribes from the real audio waveform, so timing and content are verified against what's actually in the file rather than trusted from metadata.",
  },
  {
    q: "How does language detection work?",
    a: "Several 30-second windows are sampled across the middle of the file's runtime (skipping the first/last 10%, where cold opens and credits live) and aggregated by confidence, rather than trusting a single sample — a title sequence or recap dubbed differently from the main episode can otherwise cause a misdetection.",
  },
  {
    q: "What is TVDB glossary auto-protection?",
    a: "If a file is registered from a library path containing a \"{tvdb-XXXXX}\" folder segment, the real cast list for that series is fetched from TVDB and character names are protected from being mistranslated as ordinary words during translation (e.g. a character named after a common noun).",
  },
  {
    q: "Why did a job's QC report show \"passed: false\"?",
    a: "QC findings are advisory, not blocking — flagged issues (timing, translation length, repetition) are recorded for review but never silently drop content or fail the job. Check the job's log for the specific findings.",
  },
];

export default function HelpPage() {
  return (
    <div className="content">
      <div style={{ fontSize: 15, fontWeight: 800 }}>Help</div>
      <div className="card" style={{ padding: 18, display: "flex", flexDirection: "column", gap: 16 }}>
        {FAQ.map((item) => (
          <div key={item.q}>
            <div style={{ fontWeight: 700, fontSize: 13.5, marginBottom: 4 }}>{item.q}</div>
            <div className="muted" style={{ fontSize: 12.5, lineHeight: 1.6 }}>{item.a}</div>
          </div>
        ))}
      </div>
      <div className="muted" style={{ fontSize: 12 }}>
        SubtitleAI is self-hosted and open source. For deeper technical detail, see ARCHITECTURE.md and RUNBOOK.md
        in the project repository.
      </div>
    </div>
  );
}
