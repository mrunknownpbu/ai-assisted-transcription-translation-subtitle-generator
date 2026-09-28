import type { CastReport } from "../api/types";

/** What cast_enrichment.py found the last time it checked this series:
 * every credited character, whether the subtitles use the name, whether
 * the translation engine loses it unprotected, and what was decided. */
export function CastReportPanel({ report }: { report: CastReport | null | undefined }) {
  if (!report) {
    return (
      <p className="lang-info">
        Not checked yet. The worker checks each series while idle (every 30 days by default).
      </p>
    );
  }
  const checked = new Date(report.checked_at * 1000).toLocaleString();
  if (report.error) {
    return (
      <p className="lang-info">
        Last check ({checked}) failed: {report.error}. It retries in about a day.
      </p>
    );
  }
  const candidates = [...(report.candidates ?? [])].sort(
    (a, b) =>
      Number(b.decision === "protect") - Number(a.decision === "protect") ||
      b.name_lines - a.name_lines,
  );
  return (
    <>
      <p className="lang-info">
        Checked {checked}
        {report.added?.length ? ` — protected: ${report.added.join(", ")}` : " — nothing new protected"}.
      </p>
      {report.flags?.map((flag) => (
        <p key={flag} className="lang-info">
          Note: {flag}
        </p>
      ))}
      {candidates.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Character</th>
                <th>Credited by</th>
                <th>Episodes</th>
                <th>Used as a name</th>
                <th>Lost unprotected</th>
                <th>Decision</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((c) => (
                <tr key={c.name}>
                  <td>
                    <strong>{c.name}</strong>
                    {c.full_names.length > 0 && <div className="lang-info">{c.full_names.join(", ")}</div>}
                  </td>
                  <td>{c.sources.join(", ")}</td>
                  <td>{c.scope?.length ? c.scope.join(", ") : "All"}</td>
                  <td>
                    {c.name_lines} lines / {c.name_episodes} ep.
                  </td>
                  <td
                    title={c.examples.map((e) => `${e.source} → ${e.unprotected}`).join("\n")}
                  >
                    {c.probe ?? "—"}
                  </td>
                  <td>
                    <strong>{c.decision === "protect" ? "Protected" : "Not protected"}</strong>
                    <div className="lang-info">{c.reason}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
