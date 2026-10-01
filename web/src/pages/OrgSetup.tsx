// Org setup wizard — the pasted product spec's "SET UP YOUR ORGANIZATION" mockup, as a
// real step flow instead of one flat page (the "step by step flow is still not smooth"
// feedback).
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
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { INTEGRATION_ICON } from "../components/Icons";
import type { ConnectionKind, ConnectionOut, DepartmentOut } from "../types";

const STEPS = ["Teams", "Sources", "Review"] as const;
type Step = (typeof STEPS)[number];

const SOURCE_LABELS: Record<ConnectionKind, string> = {
  slack: "Slack",
  drive: "Google Drive",
  email: "Gmail",
  call: "Call transcripts",
  directory: "Directory",
};

const UNAVAILABLE_SOURCES: { key: string; name: string }[] = [
  { key: "linear", name: "Linear / Jira" },
  { key: "github", name: "GitHub" },
  { key: "crm", name: "CRM (Salesforce)" },
];

function statusLabel(c: ConnectionOut): string {
  if (c.status === "connected") return `Connected — ${c.seed_rows} items`;
  if (c.status === "error") return c.last_error ?? "error";
  return "Not connected";
}

export function OrgSetup() {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>("Teams");
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

  const connectedCount = connections?.filter((c) => c.status === "connected").length ?? 0;
  const stepIndex = STEPS.indexOf(step);

  return (
    <div className="org-setup-page">
      <header className="page-head">
        <h2>Set up your organization</h2>
      </header>

      <div className="wizard-steps">
        {STEPS.map((s, i) => (
          <div key={s} className={`wizard-step ${i === stepIndex ? "active" : ""} ${i < stepIndex ? "done" : ""}`}>
            <span className="wizard-step-dot">{i < stepIndex ? "✓" : i + 1}</span>
            {s}
          </div>
        ))}
      </div>

      {error && <div className="error">{error}</div>}

      {step === "Teams" && (
        <section className="setup-section glass">
          <h3>Teams</h3>
          <p className="legend">
            Fixed for MVP — configurable departments are a planned follow-up
            (ARCHITECTURE-v2.md §7.1), not built yet.
          </p>
          <div className="checkbox-grid">
            {departments?.map((d) => (
              <label key={d.slug} className="checkbox-row disconnected disabled">
                <input type="checkbox" checked disabled />
                {d.name}
              </label>
            ))}
            <label className="checkbox-row disconnected disabled muted">
              <input type="checkbox" disabled />
              Finance (not tracked)
            </label>
          </div>
          <div className="wizard-actions">
            <button className="btn primary" onClick={() => setStep("Sources")}>
              Continue
            </button>
          </div>
        </section>
      )}

      {step === "Sources" && (
        <section className="setup-section glass">
          <h3>Sources</h3>
          <p className="legend">
            Every source below runs in <b>simulation mode</b> — seeded data stored in
            Postgres, shaped like a real API response. Checking a box writes a real
            connection row.
          </p>
          <div className="checkbox-grid">
            {connections?.map((c) => {
              const connected = c.status === "connected";
              const Icon = INTEGRATION_ICON[c.kind];
              return (
                <label key={c.kind} className={`checkbox-row ${c.status}`}>
                  <input
                    type="checkbox"
                    checked={connected}
                    disabled={pending === c.kind}
                    onChange={() => toggle(c.kind, connected)}
                  />
                  {Icon && (
                    <span className="checkbox-icon">
                      <Icon size={18} />
                    </span>
                  )}
                  <span>
                    {SOURCE_LABELS[c.kind]}
                    <span className="conn-status muted"> — {statusLabel(c)}</span>
                  </span>
                </label>
              );
            })}
            {UNAVAILABLE_SOURCES.map(({ key, name }) => {
              const Icon = INTEGRATION_ICON[key];
              return (
                <label key={key} className="checkbox-row disconnected disabled muted">
                  <input type="checkbox" disabled />
                  {Icon && (
                    <span className="checkbox-icon">
                      <Icon size={18} />
                    </span>
                  )}
                  <span>
                    {name}
                    <span className="conn-status muted"> — no connector built yet</span>
                  </span>
                </label>
              );
            })}
          </div>
          <div className="wizard-actions">
            <button className="btn" onClick={() => setStep("Teams")}>
              Back
            </button>
            <button className="btn primary" onClick={() => setStep("Review")}>
              Continue
            </button>
          </div>
        </section>
      )}

      {step === "Review" && (
        <section className="setup-section glass">
          <h3>You're set</h3>
          <p className="legend">
            {connectedCount} source{connectedCount === 1 ? "" : "s"} connected,{" "}
            {departments?.length ?? 0} teams tracked. You can change this anytime from
            Setup.
          </p>
          <div className="wizard-actions">
            <button className="btn" onClick={() => setStep("Sources")}>
              Back
            </button>
            <button className="btn primary" onClick={() => navigate("/me")}>
              Go to dashboard
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
