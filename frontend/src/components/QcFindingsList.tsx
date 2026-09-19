import type { JobQc } from "../api/types";

/** Renders qc.<stage>.findings[] in full -- category/reason/confidence/
 * evidence per flagged cue. The old GUI only ever showed "N flagged" /
 * "clean"; this is the whole reason the redesign happened. */
export function QcFindingsList({ qc }: { qc: JobQc }) {
  const stages = Object.entries(qc).filter(([, stage]) => stage && stage.findings.length > 0);

  if (stages.length === 0) {
    return <p className="lang-info">No QC findings.</p>;
  }

  return (
    <>
      {stages.map(([name, stage]) => (
        <div key={name}>
          <h3>
            {name} &mdash; {stage!.flagged} / {stage!.population} flagged
          </h3>
          {stage!.findings.map((f, i) => (
            <div key={i} className="finding">
              <div className="finding-category">{f.category}</div>
              <div className="finding-reason">
                cue #{f.index}: {f.reason}
              </div>
              <div className="finding-confidence">confidence {(f.confidence * 100).toFixed(0)}%</div>
              {Object.keys(f.evidence).length > 0 && (
                <div className="finding-confidence">{JSON.stringify(f.evidence)}</div>
              )}
            </div>
          ))}
        </div>
      ))}
    </>
  );
}
