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
import type { IncidentContextPackOut, IncidentSummaryOut } from "../types";

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
        <span className="incident-badge">P0</span>
        <div>
          <h2>{pack.incident_id}</h2>
          <div className="sub">
            Affects: {pack.entities.join(", ") || "no customer identified"}
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
        {pack.similar_past_incidents.length === 0 ? (
          <p className="muted">
            None in this account's history yet — only one incident exists in the current
            dataset.
          </p>
        ) : (
          <ul>
            {pack.similar_past_incidents.map((id) => (
              <li key={id}>{id}</li>
            ))}
          </ul>
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
            <span className="incident-badge small">P0</span>
            <div>
              <div className="incident-list-id">{i.incident_id}</div>
              <div className="sub">
                {i.entities.join(", ") || "unidentified customer"} ·{" "}
                {i.first_seen_at ? new Date(i.first_seen_at).toLocaleString() : "no timestamp"} ·{" "}
                {i.object_count} linked item{i.object_count === 1 ? "" : "s"}
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
