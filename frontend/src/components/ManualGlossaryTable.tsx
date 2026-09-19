import { useState } from "react";

import { useDeleteGlossaryEntity, useUpdateGlossaryEntity } from "../api/hooks";
import { ApiError } from "../api/client";
import type { GlossaryEntity } from "../api/types";
import { useToast } from "./Toast";

/** Editable view of a series' hand-curated glossary -- previously a
 * read-only <ul>. Every real fix this session (Evren, Yıldız, Fikret)
 * required hand-editing the YAML file on the host directly; this gives
 * the same edit/remove actions a real GUI path. */
export function ManualGlossaryTable({
  tvdbId,
  entries,
}: {
  tvdbId: number;
  entries: GlossaryEntity[];
}) {
  const { notify } = useToast();
  const update = useUpdateGlossaryEntity(tvdbId);
  const del = useDeleteGlossaryEntity(tvdbId);
  const [editing, setEditing] = useState<string | null>(null);
  const [canonicalInput, setCanonicalInput] = useState("");
  const [aliasesInput, setAliasesInput] = useState("");
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  // Hides a row the instant its deletion succeeds, rather than waiting for
  // the parent's refetch (react-query invalidation) to come back -- same
  // reasoning as GlossarySuggestionsTable's justPromoted set.
  const [justDeleted, setJustDeleted] = useState<Set<string>>(new Set());

  const visible = entries.filter((e) => !justDeleted.has(e.canonical));

  if (visible.length === 0) {
    return <p className="lang-info">No hand-curated glossary entries for this series.</p>;
  }

  const startEdit = (entry: GlossaryEntity) => {
    setEditing(entry.canonical);
    setCanonicalInput(entry.canonical);
    setAliasesInput(entry.surface_forms.filter((f) => f !== entry.canonical).join(", "));
    setConfirmingDelete(null);
  };

  const cancelEdit = () => setEditing(null);

  const saveEdit = (original: string) => {
    const canonical = canonicalInput.trim();
    const aliases = aliasesInput
      .split(",")
      .map((a) => a.trim())
      .filter(Boolean);
    update.mutate(
      { original_canonical: original, canonical, aliases },
      {
        onSuccess: () => {
          setEditing(null);
          notify(`Updated "${canonical}".`);
        },
        onError: (err) => notify(err instanceof ApiError ? err.message : "Update failed", "error"),
      },
    );
  };

  const confirmDelete = (canonical: string) => {
    del.mutate(
      { canonical },
      {
        onSuccess: () => {
          setConfirmingDelete(null);
          setJustDeleted((prev) => new Set(prev).add(canonical));
          notify(`Removed "${canonical}" from the glossary.`);
        },
        onError: (err) => notify(err instanceof ApiError ? err.message : "Delete failed", "error"),
      },
    );
  };

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Aliases</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((entry) => {
            const aliases = entry.surface_forms.filter((f) => f !== entry.canonical);
            const isEditing = editing === entry.canonical;
            const isConfirming = confirmingDelete === entry.canonical;
            return (
              <tr key={entry.canonical}>
                {isEditing ? (
                  <>
                    <td>
                      <input
                        value={canonicalInput}
                        onChange={(e) => setCanonicalInput(e.target.value)}
                        aria-label="Canonical name"
                      />
                    </td>
                    <td>
                      <input
                        value={aliasesInput}
                        onChange={(e) => setAliasesInput(e.target.value)}
                        placeholder="Aliases, comma-separated"
                        aria-label="Aliases"
                      />
                    </td>
                    <td>
                      <button
                        className="text-button"
                        onClick={() => saveEdit(entry.canonical)}
                        disabled={update.isPending || !canonicalInput.trim()}
                      >
                        Save
                      </button>
                      <button className="text-button" onClick={cancelEdit}>
                        Cancel
                      </button>
                    </td>
                  </>
                ) : (
                  <>
                    <td>
                      <strong>{entry.canonical}</strong>
                    </td>
                    <td>{aliases.join(", ")}</td>
                    <td>
                      {isConfirming ? (
                        <>
                          <span>Really delete? </span>
                          <button
                            className="text-button"
                            onClick={() => confirmDelete(entry.canonical)}
                            disabled={del.isPending}
                          >
                            Confirm
                          </button>
                          <button className="text-button" onClick={() => setConfirmingDelete(null)}>
                            Cancel
                          </button>
                        </>
                      ) : (
                        <>
                          <button className="text-button" onClick={() => startEdit(entry)}>
                            Edit
                          </button>
                          <button
                            className="text-button"
                            onClick={() => setConfirmingDelete(entry.canonical)}
                          >
                            Delete
                          </button>
                        </>
                      )}
                    </td>
                  </>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
