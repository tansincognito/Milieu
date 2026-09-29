import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { GapOut } from "../types";

// Shared between the per-entity Gaps tab (§17.2) and the global Review queue (§17.5).
// Empty until the Day 3 handoff validator populates `context_gaps` — see web/README.md.

export function GapList({
  entity,
  onSelect,
  onError,
  onResolved,
}: {
  entity?: string;
  onSelect: (id: string) => void;
  onError: (msg: string) => void;
  /** Called after a gap is confirmed/ignored, so a parent page can refresh anything that
   * caches a stale count (e.g. the Gaps tab badge / entity header). */
  onResolved?: () => void;
}) {
  const [gaps, setGaps] = useState<GapOut[] | null>(null);

  const load = useCallback(() => {
    api
      .listGaps({ entity, status: "open" })
      .then(setGaps)
      .catch((e) => onError(e instanceof ApiError ? e.message : String(e)));
  }, [entity, onError]);

  useEffect(load, [load]);

  async function review(gapId: string, action: "confirm" | "ignore") {
    try {
      await api.reviewGap(gapId, { action });
      load();
      onResolved?.();
    } catch (e) {
      onError(e instanceof ApiError ? e.message : String(e));
    }
  }

  if (gaps === null) return <div className="empty-state">Loading…</div>;
  if (gaps.length === 0) {
    return (
      <div className="empty-state">
        No open gaps. (The handoff validator that populates gaps ships on Day 3 — this list
        renders whatever <code>/gaps</code> currently returns.)
      </div>
    );
  }

  return (
    <div>
      {gaps.map((g) => (
        <div className="gap-row" key={g.id}>
          <div className="explanation">{g.explanation}</div>
          <div className="meta" style={{ marginBottom: "0.4rem" }}>
            <span
              className={`pill ${
                g.severity_band === "high" ? "danger" : g.severity_band === "medium" ? "warn" : ""
              }`}
            >
              {g.severity_band} severity
            </span>
            <span className="pill">{g.outcome}</span>
            {g.inherited && <span className="pill">inherited</span>}
            <span className="pill">{g.contract_field}</span>
          </div>
          <div className="actions">
            <button className="btn small primary" onClick={() => review(g.id, "confirm")}>
              Confirm
            </button>
            <button className="btn small" onClick={() => review(g.id, "ignore")}>
              Ignore
            </button>
            {g.upstream && (
              <button className="btn small" onClick={() => onSelect(g.upstream!.id)}>
                View upstream
              </button>
            )}
            {g.downstream && (
              <button className="btn small" onClick={() => onSelect(g.downstream!.id)}>
                View downstream
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
