import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import type { EntityOut } from "../types";

// §17.1 Entity picker: entities with source counts per kind, plus counts of open
// gaps and conflicts.

export function EntityPicker() {
  const [entities, setEntities] = useState<EntityOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  function load() {
    api
      .listEntities()
      .then(setEntities)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }

  useEffect(load, []);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Entities</h1>
          <div className="sub">Source coverage and open items per entity (§17.1)</div>
        </div>
        <button className="btn" onClick={load}>
          Refresh
        </button>
      </div>

      {error && <div className="banner error">{error}</div>}

      {entities === null && !error && <div className="empty-state">Loading…</div>}

      {entities && entities.length === 0 && (
        <div className="empty-state">
          No entities yet. Load mock data from the top bar, wait for extraction to run, then
          refresh.
        </div>
      )}

      {entities && entities.length > 0 && (
        <div className="entity-grid">
          {entities.map((e) => {
            const totalSources = Object.values(e.source_counts).reduce((a, b) => a + b, 0);
            return (
              <div key={e.id} className="entity-card" onClick={() => navigate(`/entities/${e.id}`)}>
                <h3>{e.name}</h3>
                <div className="kind">{e.kind}</div>
                <div className="counts">
                  {Object.entries(e.source_counts).map(([kind, count]) => (
                    <span className="pill" key={kind}>
                      {kind}: {count}
                    </span>
                  ))}
                  {totalSources === 0 && <span className="pill">no sources yet</span>}
                </div>
                <div className="flags">
                  <span className={`pill ${e.open_conflicts > 0 ? "danger" : "ok"}`}>
                    {e.open_conflicts} open conflict{e.open_conflicts === 1 ? "" : "s"}
                  </span>
                  <span className={`pill ${e.open_gaps > 0 ? "warn" : "ok"}`}>
                    {e.open_gaps} open gap{e.open_gaps === 1 ? "" : "s"}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
