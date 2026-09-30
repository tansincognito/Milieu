// Org setup screen — the pasted product spec's "SET UP YOUR ORGANIZATION" mockup.
//
// Source checkboxes are real: toggling one writes to `connections` (migration 0006) and
// the status shown is the server's honest answer, including "error — no simulation seed
// data available" for a source (Linear, Salesforce) that nothing in this repo seeds yet.
//
// Team checkboxes are shown checked and disabled, not fake-interactive: departments are
// still the fixed four-stage CHECK constraint (STAGES in orm.py), not yet a configurable,
// per-tenant table (ARCHITECTURE-v2.md §7.1). A checkbox a click does nothing to would be
// worse than no checkbox at all.

import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { ConnectionKind, ConnectionOut, DepartmentOut } from "../types";

const SOURCE_LABELS: Record<ConnectionKind, string> = {
  slack: "Slack",
  drive: "Google Drive",
  email: "Gmail",
  call: "Call transcripts",
  directory: "Directory (AD / Entra / Okta / Workspace)",
};

// Sources the pasted mockup names that this repo has no seed data for at all — shown as
// real, unavailable rows rather than omitted, so the screen doesn't quietly undersell the
// gap between the vision and what's connectable today.
const UNAVAILABLE_SOURCES = ["Linear / Jira", "GitHub", "CRM (Salesforce)"];

function statusLabel(c: ConnectionOut): string {
  if (c.status === "connected") return `Connected — ${c.seed_rows} items`;
  if (c.status === "error") return c.last_error ?? "error";
  return "Not connected";
}

export function OrgSetup() {
  const [connections, setConnections] = useState<ConnectionOut[] | null>(null);
  const [departments, setDepartments] = useState<DepartmentOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<ConnectionKind | null>(null);

  function load() {
    Promise.all([api.listConnections(), api.listDepartments()])
      .then(([c, d]) => {
        setConnections(c);
        setDepartments(d);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }

  useEffect(load, []);

  async function toggle(kind: ConnectionKind, currentlyConnected: boolean) {
    setPending(kind);
    setError(null);
    try {
      await api.setConnection(kind, !currentlyConnected);
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="org-setup-page">
      <header className="page-head">
        <h2>Set up your organization</h2>
      </header>

      {error && <div className="error">{error}</div>}

      <section className="setup-section">
        <h3>Teams</h3>
        <p className="legend">
          Fixed for MVP — configurable departments are a planned follow-up
          (ARCHITECTURE-v2.md §7.1), not built yet.
        </p>
        <div className="checkbox-grid">
          {departments?.map((d) => (
            <label key={d.slug} className="checkbox-row disabled">
              <input type="checkbox" checked disabled />
              {d.name}
            </label>
          ))}
          <label className="checkbox-row disabled muted">
            <input type="checkbox" disabled />
            Finance (not tracked)
          </label>
        </div>
      </section>

      <section className="setup-section">
        <h3>Sources</h3>
        <p className="legend">
          Every source below runs in <b>simulation mode</b> — seeded data stored in Postgres,
          shaped like a real API response. Checking a box writes a real connection row.
        </p>
        <div className="checkbox-grid">
          {connections?.map((c) => {
            const connected = c.status === "connected";
            return (
              <label key={c.kind} className={`checkbox-row ${c.status}`}>
                <input
                  type="checkbox"
                  checked={connected}
                  disabled={pending === c.kind}
                  onChange={() => toggle(c.kind, connected)}
                />
                <span>
                  {SOURCE_LABELS[c.kind]}
                  <span className="conn-status muted"> — {statusLabel(c)}</span>
                </span>
              </label>
            );
          })}
          {UNAVAILABLE_SOURCES.map((name) => (
            <label key={name} className="checkbox-row disabled muted">
              <input type="checkbox" disabled />
              <span>
                {name}
                <span className="conn-status muted"> — no connector built yet</span>
              </span>
            </label>
          ))}
        </div>
      </section>
    </div>
  );
}
