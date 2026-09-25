import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { formatActor } from "../format";
import type { ContextObjectOut, ConflictPairOut } from "../types";

// Shared between the per-entity Conflicts tab (§17.2) and the global Review queue (§17.5).
// "Resolve Conflict" here always means R5 human resolution (§7.3): pick a winner (the
// loser supersedes) or supersede both.

export function ConflictList({
  entity,
  onSelect,
  onError,
  onResolved,
}: {
  entity?: string;
  onSelect: (id: string) => void;
  onError: (msg: string) => void;
  /** Called after a conflict is resolved, so a parent page can refresh anything that
   * caches a stale count (e.g. the Conflicts tab badge / entity header). */
  onResolved?: () => void;
}) {
  const [conflicts, setConflicts] = useState<ConflictPairOut[] | null>(null);
  const [busyRelation, setBusyRelation] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .listConflicts(entity)
      .then(setConflicts)
      .catch((e) => onError(e instanceof ApiError ? e.message : String(e)));
  }, [entity, onError]);

  useEffect(load, [load]);

  async function resolve(relationId: string, winnerId: string | null) {
    setBusyRelation(relationId);
    try {
      await api.resolveConflict(
        relationId,
        winnerId ? { resolution: "winner", winner_id: winnerId } : { resolution: "both_superseded" },
      );
      load();
      onResolved?.();
    } catch (e) {
      onError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusyRelation(null);
    }
  }

  if (conflicts === null) return <div className="empty-state">Loading…</div>;
  if (conflicts.length === 0) return <div className="empty-state">No open conflicts.</div>;

  return (
    <div>
      {conflicts.map((pair) => (
        <div className="conflict-pair" key={pair.relation_id}>
          <div className="pair-objs">
            <ConflictSide obj={pair.from_object} onClick={() => onSelect(pair.from_object.id)} />
            <ConflictSide obj={pair.to_object} onClick={() => onSelect(pair.to_object.id)} />
          </div>
          <div className="actions">
            <button
              className="btn primary small"
              disabled={busyRelation === pair.relation_id}
              onClick={() => resolve(pair.relation_id, pair.from_object.id)}
            >
              {formatActor(pair.from_object.actor_label)} wins
            </button>
            <button
              className="btn primary small"
              disabled={busyRelation === pair.relation_id}
              onClick={() => resolve(pair.relation_id, pair.to_object.id)}
            >
              {formatActor(pair.to_object.actor_label)} wins
            </button>
            <button
              className="btn small"
              disabled={busyRelation === pair.relation_id}
              onClick={() => resolve(pair.relation_id, null)}
            >
              Supersede both
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

function ConflictSide({ obj, onClick }: { obj: ContextObjectOut; onClick: () => void }) {
  return (
    <div className="side" onClick={onClick}>
      <div style={{ fontSize: "0.85rem", marginBottom: "0.25rem" }}>{obj.content}</div>
      <div className="meta">
        <span>{formatActor(obj.actor_label)}</span>
        <span>· authority {obj.authority}</span>
        <span>· {obj.stage ?? "no stage"}</span>
      </div>
    </div>
  );
}
