import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { ConflictList } from "../components/ConflictList";
import { DetailPanel } from "../components/DetailPanel";
import { GapList } from "../components/GapList";
import { AuthorityBadge, ConfidenceBadge } from "../components/Badges";
import { formatActor } from "../format";
import type { ContextObjectOut, EntityOut } from "../types";

// §17.5 Review queue: candidates, conflicts, and gaps. Actions: Confirm, Edit, Ignore,
// Resolve Conflict, Mark Stale. Edits create a new version and leave evidence untouched
// (enforced server-side in POST /context/{id}/review — see api/app/api/context.py).

export function ReviewQueue() {
  const navigate = useNavigate();
  const [candidates, setCandidates] = useState<ContextObjectOut[] | null>(null);
  const [entities, setEntities] = useState<Record<string, EntityOut>>({});
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [conflictKey, setConflictKey] = useState(0);
  const [gapKey, setGapKey] = useState(0);

  const loadCandidates = useCallback(() => {
    api
      .searchContext({ status: "candidate", limit: 200 })
      .then(setCandidates)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  useEffect(() => {
    loadCandidates();
    api
      .listEntities()
      .then((list) => setEntities(Object.fromEntries(list.map((e) => [e.id, e]))))
      .catch(() => {});
  }, [loadCandidates]);

  async function quickAction(id: string, action: "confirm" | "ignore" | "mark_stale") {
    setBusyId(id);
    try {
      await api.reviewContext(id, { action });
      loadCandidates();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  function handleDetailChanged() {
    loadCandidates();
    setConflictKey((k) => k + 1);
    setGapKey((k) => k + 1);
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Review queue</h1>
          <div className="sub">Candidates, conflicts, and gaps across every entity (§17.5)</div>
        </div>
      </div>

      {error && <div className="banner error">{error}</div>}

      <div className="queue-section">
        <h3>Candidates {candidates ? `(${candidates.length})` : ""}</h3>
        {candidates === null && <div className="empty-state">Loading…</div>}
        {candidates?.length === 0 && <div className="empty-state">No candidates awaiting review.</div>}
        {candidates?.map((c) => (
          <div className="obj-row" key={c.id} onClick={() => setSelected(c.id)}>
            <div className="obj-main">
              <div className="content">{c.content}</div>
              <div className="meta">
                <span>{entities[c.entity_id]?.name ?? c.entity_id.slice(0, 8)}</span>
                <span>· {c.subject_key}</span>
                <AuthorityBadge authority={c.authority} />
                <ConfidenceBadge confidence={c.confidence} />
                <span>· {formatActor(c.actor_label)}</span>
              </div>
              <div
                className="review-actions"
                onClick={(e) => e.stopPropagation()}
                style={{ marginTop: "0.5rem" }}
              >
                <button
                  className="btn small primary"
                  disabled={busyId === c.id}
                  onClick={() => quickAction(c.id, "confirm")}
                >
                  Confirm
                </button>
                <button className="btn small" onClick={() => setSelected(c.id)}>
                  Edit
                </button>
                <button
                  className="btn small"
                  disabled={busyId === c.id}
                  onClick={() => quickAction(c.id, "ignore")}
                >
                  Ignore
                </button>
                <button
                  className="btn small danger"
                  disabled={busyId === c.id}
                  onClick={() => quickAction(c.id, "mark_stale")}
                >
                  Mark Stale
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="queue-section">
        <h3>Conflicts</h3>
        <ConflictList key={conflictKey} onSelect={setSelected} onError={setError} />
      </div>

      <div className="queue-section">
        <h3>Gaps</h3>
        <GapList key={gapKey} onSelect={setSelected} onError={setError} />
      </div>

      {selected && (
        <DetailPanel
          contextId={selected}
          onClose={() => setSelected(null)}
          onNavigate={(id) => navigate(`/context/${id}`)}
          onChanged={handleDetailChanged}
        />
      )}
    </div>
  );
}
