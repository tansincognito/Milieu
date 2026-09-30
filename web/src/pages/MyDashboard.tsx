// The personalized landing page: "potential contradictions, decisions pending, deadlines
// incoming, context degradation, incident context pack" (pasted product spec), scoped to
// the logged-in person's team by default, with a toggle to the org-wide (C-suite) view.
//
// Every number here comes from GET /dashboard, which runs real queries against
// context_objects/context_gaps -- see api/app/api/dashboard.py's module docstring.

import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { clearSession, loadSession } from "../session";
import type { DashboardOut, DashboardScope } from "../types";

function DueBadge({ date, precision, overdue }: { date: string; precision: string | null; overdue: boolean }) {
  const label = precision === "month" ? date.slice(0, 7) : precision === "quarter" ? date.slice(0, 7) : date;
  return <span className={`due-badge ${overdue ? "overdue" : ""}`}>{overdue ? "overdue · " : ""}{label}</span>;
}

export function MyDashboard() {
  const navigate = useNavigate();
  // `loadSession()` re-parses sessionStorage into a NEW object on every call, so calling it
  // directly in the render body and putting the result in the effect's dependency array
  // made the effect think `person` changed on every render, causing an infinite
  // fetch-then-unmount loop (cards would render, then vanish, forever). Read it once, into
  // state, so its identity is stable across re-renders.
  const [person] = useState(() => loadSession());
  const [scope, setScope] = useState<DashboardScope>("personal");
  const [data, setData] = useState<DashboardOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!person) {
      navigate("/login");
      return;
    }
    setData(null);
    api
      .getDashboard(scope, person.team)
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [scope, person, navigate]);

  if (!person) return null;

  const canOrgView = person.role?.match(/^(CEO|CPO|CRO|COO|CFO)$/i) || person.team === "leadership";

  return (
    <div className="my-dashboard">
      <header className="dash-header">
        <div>
          <div className="dash-greeting">Hi {person.name.split(" ")[0]}</div>
          <div className="sub">
            {person.role} · {person.team ?? "no team on file"}
          </div>
        </div>
        <div className="dash-header-actions">
          {canOrgView && (
            <div className="scope-toggle">
              <button
                className={scope === "personal" ? "active" : ""}
                onClick={() => setScope("personal")}
              >
                My team
              </button>
              <button className={scope === "org" ? "active" : ""} onClick={() => setScope("org")}>
                Whole org
              </button>
            </div>
          )}
          <button
            className="btn small"
            onClick={() => {
              clearSession();
              navigate("/login");
            }}
          >
            Sign out
          </button>
        </div>
      </header>

      {error && <div className="error">{error}</div>}
      {!data && !error && <p className="muted">Loading…</p>}

      {data && (
        <div className="dash-grid">
          <section className="dash-card glass">
            <h3>Potential contradictions</h3>
            {data.contradictions.length === 0 ? (
              <p className="muted small">None right now.</p>
            ) : (
              data.contradictions.slice(0, 5).map((o) => (
                <Link key={o.id} to={`/context/${o.id}`} className="dash-row">
                  <span className="dash-row-type">{o.type}</span>
                  {o.content}
                </Link>
              ))
            )}
            {data.contradictions.length > 5 && (
              <div className="dash-more">+{data.contradictions.length - 5} more</div>
            )}
          </section>

          <section className="dash-card glass">
            <h3>Decisions pending</h3>
            {data.decisions_pending.length === 0 ? (
              <p className="muted small">Nothing awaiting review.</p>
            ) : (
              data.decisions_pending.slice(0, 5).map((o) => (
                <Link key={o.id} to={`/context/${o.id}`} className="dash-row">
                  <span className="dash-row-type">{o.type}</span>
                  {o.content}
                </Link>
              ))
            )}
          </section>

          <section className="dash-card glass">
            <h3>Deadlines incoming</h3>
            {data.deadlines_incoming.length === 0 ? (
              <p className="muted small">Nothing on the calendar.</p>
            ) : (
              data.deadlines_incoming.slice(0, 5).map((d) => (
                <Link key={d.object.id} to={`/context/${d.object.id}`} className="dash-row">
                  <DueBadge date={d.due_date} precision={d.due_date_precision} overdue={d.overdue} />
                  {d.object.content}
                </Link>
              ))
            )}
          </section>

          <section className="dash-card glass">
            <h3>Context degradation</h3>
            <div className="dash-stat">{data.degradation_count}</div>
            <p className="muted small">open gap{data.degradation_count === 1 ? "" : "s"} across handoffs</p>
            <Link to="/flows" className="dash-link">
              See organizational flows →
            </Link>
          </section>

          <section className="dash-card glass">
            <h3>Incident context pack{data.incidents.length === 1 ? "" : "s"}</h3>
            {data.incidents.length === 0 ? (
              <p className="muted small">No active incidents.</p>
            ) : (
              data.incidents.map((id) => (
                <Link key={id} to={`/incidents/${encodeURIComponent(id)}`} className="dash-row">
                  <span className="incident-badge small">P0</span>
                  {id}
                </Link>
              ))
            )}
          </section>
        </div>
      )}
    </div>
  );
}
