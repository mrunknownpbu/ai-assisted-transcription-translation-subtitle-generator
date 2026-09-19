import { useState } from "react";

import { usePromoteGlossaryEntity } from "../api/hooks";
import { ApiError } from "../api/client";
import type { AutoSuggestion } from "../api/types";
import { useToast } from "./Toast";

/** First-ever GUI visibility into auto_glossary.py's mined candidates --
 * previously a write-only YAML file
 * (/cache/glossary_suggestions/<tvdb_id>.yaml) nothing ever read back. */
export function GlossarySuggestionsTable({
  suggestions,
  tvdbId,
}: {
  suggestions: AutoSuggestion[];
  tvdbId: number;
}) {
  const { notify } = useToast();
  const promote = usePromoteGlossaryEntity(tvdbId);
  // Hides a row the instant its promotion succeeds, rather than waiting
  // for the next mined-episode job to naturally exclude it (see
  // auto_glossary.py's exclude_canonicals wiring) -- avoids a stale-
  // looking "suggestion" for a name you already just promoted.
  const [justPromoted, setJustPromoted] = useState<Set<string>>(new Set());

  const visible = suggestions.filter((s) => !justPromoted.has(s.canonical));

  const handlePromote = (s: AutoSuggestion) => {
    promote.mutate(
      { canonical: s.canonical, aliases: s.aliases },
      {
        onSuccess: () => {
          setJustPromoted((prev) => new Set(prev).add(s.canonical));
          notify(`Promoted "${s.canonical}" -- it now affects translation.`);
        },
        onError: (err) => notify(err instanceof ApiError ? err.message : "Promote failed", "error"),
      },
    );
  };

  if (visible.length === 0) {
    return <p className="lang-info">No auto-mined suggestions yet for this series.</p>;
  }

  return (
    <div className="table-wrap">
      <p className="suggestion-note">
        Mined from this series' own completed episodes (video or SRT-translation). Promoting a name
        makes it a real, translation-affecting protected entity immediately.
      </p>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Occurrences</th>
            <th>Distinct episodes</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((s) => (
            <tr key={s.canonical}>
              <td>{s.canonical}</td>
              <td>{s.occurrences}</td>
              <td>{s.distinct_episodes}</td>
              <td>
                <button
                  className="text-button"
                  onClick={() => handlePromote(s)}
                  disabled={promote.isPending}
                >
                  Promote
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
