// Incident Context Pack — the pasted product spec's P0 mockup, backed by real data.
//
// Honesty carried through from the backend (see api/app/api/incidents.py's module
// docstring): only objects that carry a structured incident_id are "linked". Everything
// else that matches by capability and time-window alone is shown separately, labeled
// "unconfirmed" — a real signal, but not a proven one. This is a genuine limitation in
// today's pipeline (nothing stamps a live Slack incident thread with its incident_id as
// messages arrive), not a UI simplification, so it stays visible rather than hidden.

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import type { IncidentContextPackOut, IncidentSeverity, IncidentSummaryOut } from "../types";

function SeverityBadge({ severity, small }: { severity: IncidentSeverity; small?: boolean }) {
  return <span className={`incident-badge ${small ? "small" : ""}`}>{severity}</span>;
}

function SlotCard({
  title,
  object,
}: {
  title: string;
  object: IncidentContextPackOut["impact"];
}) {
  return (
    <div className="incident-slot">
      <div className="incident-slot-title">{title}</div>
      {object ? (
        <div className="incident-slot-body">{object.content}</div>
      ) : (
        <div className="incident-slot-body muted">not established</div>
      )}
    </div>
  );
}

function IncidentPackView({ incidentId }: { incidentId: string }) {
  const [pack, setPack] = useState<IncidentContextPackOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setPack(null);
    setError(null);
    api
      .getIncidentPack(incidentId)
      .then(setPack)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [incidentId]);

  if (error) return <div className="error">{error}</div>;
  if (!pack) return <p className="muted">Loading…</p>;

  const linkedCount = pack.timeline.filter((e) => e.linked).length;
  const unconfirmedCount = pack.timeline.length - linkedCount;

  return (
    <div className="incident-pack">
      <div className="incident-header">
        <SeverityBadge severity={pack.severity} />
        <div>
          <h2>
            {pack.title} <span className="muted">({pack.incident_id})</span>
          </h2>
          <div className="sub">
            {pack.status === "resolved" ? "Resolved" : "Open"} · Affects:{" "}
            {pack.entities.join(", ") || "no customer identified"}
          </div>
        </div>
      </div>

      {unconfirmedCount > 0 && (
        <p className="banner warn">
          {linkedCount} item{linkedCount === 1 ? "" : "s"} confirmed linked to this incident
          id. {unconfirmedCount} more match by topic and time window but are{" "}
          <b>unconfirmed</b> — shown below, marked separately. See why in the legend.
        </p>
      )}

      <section className="incident-slots">
        <SlotCard title="Impact" object={pack.impact} />
        <SlotCard title="SLA impact" object={pack.sla_impact} />
        <SlotCard title="Root cause" object={pack.root_cause} />
        <SlotCard title="Remediation" object={pack.remediation} />
      </section>

      <section>
        <h3>What changed</h3>
        <div className="incident-timeline">
          {pack.timeline.map((entry, i) => (
            <div key={i} className={`incident-event ${entry.linked ? "" : "unconfirmed"}`}>
              <div className="incident-event-time">
                {new Date(entry.at).toLocaleString()}
                {!entry.linked && <span className="badge-unconfirmed">unconfirmed</span>}
              </div>
              <div className="incident-event-body">
                <span className="tag">{entry.object.type}</span> {entry.object.content}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h3>People</h3>
        <div className="incident-people">
          {pack.people.map((p) => (
            <div key={`${p.actor_label}-${p.actor_role}`} className="incident-person">
              <span>{p.actor_label}</span>
              <span className="muted">
                {p.actor_role} · {p.object_count} item{p.object_count === 1 ? "" : "s"}
              </span>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h3>Previous similar incidents</h3>
        <p className="legend">
          Other resolved incidents sharing at least one affected customer — not ranked by
          similarity, just the real, honest version of this feature for now.
        </p>
        {pack.similar_past_incidents.length === 0 ? (
          <p className="muted">
            None — no other resolved incident shares a customer with this one yet.
          </p>
        ) : (
          <div className="incident-list">
            {pack.similar_past_incidents.map((s) => (
              <Link
                key={s.incident_id}
                className="incident-list-row"
                to={`/incidents/${encodeURIComponent(s.incident_id)}`}
              >
                <SeverityBadge severity={s.severity} small />
                <div>
                  <div className="incident-list-id">{s.title}</div>
                  <div className="sub">
                    {s.entities.join(", ")} · {new Date(s.declared_at).toLocaleDateString()} ·{" "}
                    {s.object_count} item{s.object_count === 1 ? "" : "s"}
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>

      {pack.open_gaps.length > 0 && (
        <p className="banner error">
          {pack.open_gaps.length} open context gap{pack.open_gaps.length === 1 ? "" : "s"} on
          this incident's outbound handoff — see the Handoff report for the affected
          account.
        </p>
      )}
    </div>
  );
}

export function IncidentPack() {
  const { incidentId } = useParams<{ incidentId: string }>();
  const [incidents, setIncidents] = useState<IncidentSummaryOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (incidentId) return;
    api
      .listIncidents()
      .then(setIncidents)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [incidentId]);

  if (incidentId) return <IncidentPackView incidentId={incidentId} />;

  return (
    <div className="incident-list-page">
      <header className="page-head">
        <h2>Incidents</h2>
      </header>
      {error && <div className="error">{error}</div>}
      {incidents === null && !error && <p className="muted">Loading…</p>}
      {incidents?.length === 0 && <p className="muted">No incidents recorded.</p>}
      <div className="incident-list">
        {incidents?.map((i) => (
          <Link
            key={i.incident_id}
            className="incident-list-row"
            to={`/incidents/${encodeURIComponent(i.incident_id)}`}
          >
            <SeverityBadge severity={i.severity} small />
            <div>
              <div className="incident-list-id">
                {i.title} <span className="muted">({i.incident_id})</span>
              </div>
              <div className="sub">
                {i.status === "resolved" ? "Resolved" : "Open"} ·{" "}
                {i.entities.join(", ") || "unidentified customer"} ·{" "}
                {new Date(i.declared_at).toLocaleString()} · {i.object_count} linked item
                {i.object_count === 1 ? "" : "s"}
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
