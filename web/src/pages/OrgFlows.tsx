// Organizational Flows screen — the pasted product spec's mockup, backed by real data.
//
// Each row is an actual loaded Context Contract (api/contracts/*.yaml), with real counts
// aggregated across every entity that has ever been validated against it: how many
// accounts this flow currently applies to, and how many open losses it has right now.
//
// The mockup framed rows as "CRO -> Sales", "CEO -> Leadership" etc. -- arbitrary
// role-pairs, not just adjacent pipeline stages. What's actually loaded today is four
// stage-to-stage contracts (sales->product, product->engineering, sales->customer_success,
// engineering->customer_facing); showing exactly those, honestly labeled, rather than
// inventing rows for flows that have no contract behind them yet.

import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { ContractDetailOut, FlowSummaryOut } from "../types";

function flowLabel(contractId: string): string {
  // "sales_to_product" -> "Sales -> Product"; "engineering_to_customer_facing::sales" ->
  // "Engineering -> Customer Facing (sales)" — the "::stage" suffix is how a contract with
  // a list `to_stage` in YAML gets expanded into one DB row per target (see
  // pipeline/contracts.py).
  const [base, variant] = contractId.split("::");
  const label = base
    .split("_to_")
    .map((s) => s.replace(/_/g, " "))
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join(" → ");
  return variant ? `${label} (${variant.replace(/_/g, " ")})` : label;
}

function bandBadges(bands: Record<string, number>) {
  const order = ["critical", "high", "medium", "low"];
  const present = order.filter((b) => bands[b]);
  if (present.length === 0) return <span className="ok">no open gaps</span>;
  return (
    <>
      {present.map((b) => (
        <span key={b} className={`band-badge band-${b}`}>
          {bands[b]} {b}
        </span>
      ))}
    </>
  );
}

function ContractDetail({ contractId, onClose }: { contractId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<ContractDetailOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    api
      .getContractDetail(contractId)
      .then(setDetail)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [contractId]);

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-header">
          <h3>{flowLabel(contractId)}</h3>
          <button className="btn small" onClick={onClose}>
            Close
          </button>
        </div>
        {error && <div className="error">{error}</div>}
        {!detail && !error && <p className="muted">Loading…</p>}
        {detail && (
          <table className="hgrid">
            <thead>
              <tr>
                <th>Field</th>
                <th>Check</th>
                <th>Importance</th>
                <th>Min authority</th>
                <th>Slots</th>
              </tr>
            </thead>
            <tbody>
              {detail.fields.map((f) => (
                <tr key={f.name}>
                  <td className="hslot">{f.name}</td>
                  <td>{f.check}</td>
                  <td>{f.importance}</td>
                  <td>{f.min_upstream_authority}</td>
                  <td className="muted">{f.slots.join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export function OrgFlows() {
  const [flows, setFlows] = useState<FlowSummaryOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openContract, setOpenContract] = useState<string | null>(null);

  useEffect(() => {
    api
      .listFlows()
      .then(setFlows)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  return (
    <div className="org-flows-page">
      <header className="page-head">
        <h2>Organizational flows</h2>
      </header>
      <p className="legend">
        How context moves between teams. Each flow is a Context Contract — a typed checklist
        of what must survive the handoff. Click one to see which accounts it applies to.
      </p>

      {error && <div className="error">{error}</div>}
      {flows === null && !error && <p className="muted">Loading…</p>}

      {flows?.length === 0 && (
        <p className="muted">No contracts are loaded. Seed `/contracts` and restart the API.</p>
      )}

      <div className="flow-list">
        {flows?.map((f) => (
          <button
            key={f.contract.id}
            className="flow-row"
            onClick={() => setOpenContract(f.contract.id)}
          >
            <div className="flow-row-main">
              <span className="flow-label">{flowLabel(f.contract.id)}</span>
              <span className="flow-sub">
                {f.contract.field_count} tracked field{f.contract.field_count === 1 ? "" : "s"} ·{" "}
                {f.entities_validated} account{f.entities_validated === 1 ? "" : "s"} checked
              </span>
            </div>
            <div className="flow-row-badges">{bandBadges(f.gaps_by_severity_band)}</div>
          </button>
        ))}
      </div>

      {openContract && (
        <ContractDetail contractId={openContract} onClose={() => setOpenContract(null)} />
      )}
    </div>
  );
}
