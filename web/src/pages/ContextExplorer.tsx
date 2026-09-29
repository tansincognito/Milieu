import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { ConflictList } from "../components/ConflictList";
import { DetailPanel } from "../components/DetailPanel";
import { GapList } from "../components/GapList";
import { ObjectRow } from "../components/ObjectRow";
import type { ContextObjectOut, ContextType, EntityContextOut } from "../types";

// §17.2 Context Explorer: tabs Current (grouped), Conflicts, Gaps, History.
// Deadlines show inline from `due_date` (see ObjectRow -> DueDateInline).

type Tab = "current" | "conflicts" | "gaps" | "history";

const GROUPS: { label: string; types: ContextType[] }[] = [
  { label: "Requirements", types: ["requirement"] },
  { label: "Decisions", types: ["decision"] },
  { label: "Constraints", types: ["constraint"] },
  { label: "Dependencies", types: ["dependency"] },
  { label: "Commitments", types: ["commitment"] },
  { label: "Problems / questions", types: ["problem", "open_question", "resolution"] },
];

export function ContextExplorer() {
  const { entityId } = useParams<{ entityId: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = (searchParams.get("tab") as Tab) ?? "current";

  const [data, setData] = useState<EntityContextOut | null>(null);
  const [history, setHistory] = useState<ContextObjectOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [conflictListKey, setConflictListKey] = useState(0);
  const [gapListKey, setGapListKey] = useState(0);

  const loadCurrent = useCallback(() => {
    if (!entityId) return;
    api
      .getEntityContext(entityId)
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [entityId]);

  const loadHistory = useCallback(() => {
    if (!entityId) return;
    Promise.all([
      api.searchContext({ entity: entityId, status: "superseded", limit: 200 }),
      api.searchContext({ entity: entityId, status: "stale", limit: 200 }),
    ])
      .then(([superseded, stale]) =>
        setHistory(
          [...superseded, ...stale].sort(
            (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
          ),
        ),
      )
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [entityId]);

  useEffect(() => {
    setError(null);
    loadCurrent();
  }, [loadCurrent]);

  useEffect(() => {
    setError(null);
    if (tab === "history") loadHistory();
  }, [tab, loadHistory]);

  function setTab(t: Tab) {
    setSearchParams({ tab: t });
  }

  function handleChanged() {
    loadCurrent();
    if (tab === "history") loadHistory();
    // ConflictList/GapList manage their own data; bump their keys to force a refetch
    // after a review action taken from the detail panel drawer.
    setConflictListKey((k) => k + 1);
    setGapListKey((k) => k + 1);
  }

  if (!entityId) return null;

  const objectsByType = new Map<ContextType, ContextObjectOut[]>();
  for (const group of data?.current ?? []) {
    objectsByType.set(group.type, group.objects);
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>{data?.entity.name ?? "Entity"}</h1>
          <div className="sub">
            {data
              ? `${data.entity.kind} · ${data.entity.open_conflicts} open conflicts · ${data.entity.open_gaps} open gaps`
              : "Loading…"}
          </div>
        </div>
        <div className="page-header-actions">
          <button className="btn" onClick={() => navigate(`/entities/${entityId}/handoffs`)}>
            Handoff report →
          </button>
          <button className="btn" onClick={() => navigate("/")}>
            ← All entities
          </button>
        </div>
      </div>

      {error && <div className="banner error">{error}</div>}

      <div className="tabs">
        <button className={tab === "current" ? "active" : ""} onClick={() => setTab("current")}>
          Current
        </button>
        <button className={tab === "conflicts" ? "active" : ""} onClick={() => setTab("conflicts")}>
          Conflicts
          {data && <span className="badge">({data.entity.open_conflicts})</span>}
        </button>
        <button className={tab === "gaps" ? "active" : ""} onClick={() => setTab("gaps")}>
          Gaps
          {data && <span className="badge">({data.entity.open_gaps})</span>}
        </button>
        <button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>
          History
        </button>
      </div>

      {tab === "current" && (
        <div>
          {!data && <div className="empty-state">Loading…</div>}
          {data &&
            GROUPS.map((g) => {
              const objs = g.types.flatMap((t) => objectsByType.get(t) ?? []);
              if (objs.length === 0) return null;
              return (
                <div className="group" key={g.label}>
                  <h4>
                    {g.label} ({objs.length})
                  </h4>
                  {objs.map((o) => (
                    <ObjectRow key={o.id} obj={o} onClick={setSelected} />
                  ))}
                </div>
              );
            })}
          {data && data.current.length === 0 && (
            <div className="empty-state">No active context yet for this entity.</div>
          )}
        </div>
      )}

      {tab === "conflicts" && (
        <ConflictList
          key={conflictListKey}
          entity={entityId}
          onSelect={setSelected}
          onError={setError}
          onResolved={loadCurrent}
        />
      )}

      {tab === "gaps" && (
        <GapList key={gapListKey} entity={entityId} onSelect={setSelected} onError={setError} onResolved={loadCurrent} />
      )}

      {tab === "history" && (
        <div>
          {history === null && <div className="empty-state">Loading…</div>}
          {history?.length === 0 && <div className="empty-state">No superseded or stale objects yet.</div>}
          {history?.map((o) => (
            <ObjectRow key={o.id} obj={o} onClick={setSelected} />
          ))}
        </div>
      )}

      {selected && (
        <DetailPanel
          contextId={selected}
          onClose={() => setSelected(null)}
          onNavigate={(id) => navigate(`/context/${id}`)}
          onChanged={handleChanged}
        />
      )}
    </div>
  );
}
