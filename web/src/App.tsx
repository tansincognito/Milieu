import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, ApiError } from "./api";
import { ContextExplorer } from "./pages/ContextExplorer";
import { ContextStandalone } from "./pages/ContextStandalone";
import { EntityPicker } from "./pages/EntityPicker";
import { HandoffReport } from "./pages/HandoffReport";
import { IncidentPack } from "./pages/IncidentPack";
import { OrgFlows } from "./pages/OrgFlows";
import { OrgSetup } from "./pages/OrgSetup";
import { ReviewQueue } from "./pages/ReviewQueue";
import type { JobStats, TenantMode } from "./types";

// Simulation/production switch (migration 0006, §5). Production is a real state the
// backend accepts as a value but refuses to activate (409) until a real connector exists —
// this component shows that refusal plainly rather than silently ignoring the click.
function ModeToggle() {
  const [mode, setMode] = useState<TenantMode | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    api.getTenant().then((t) => setMode(t.mode)).catch(() => {});
  }, []);

  async function flip() {
    if (mode === null) return;
    const next: TenantMode = mode === "simulation" ? "production" : "simulation";
    setBusy(true);
    setMsg(null);
    try {
      const tenant = await api.setTenantMode({ mode: next });
      setMode(tenant.mode);
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (mode === null) return null;

  return (
    <div className="mode-toggle-wrap">
      <button
        type="button"
        className={`mode-toggle ${mode}`}
        disabled={busy}
        onClick={flip}
        title={mode === "simulation" ? "Running on seeded data" : "Running on real sources"}
      >
        <span className="mode-toggle-dot" />
        {mode === "simulation" ? "Simulation" : "Production"}
      </button>
      {msg && <span className="mode-toggle-msg">{msg}</span>}
    </div>
  );
}

function TopNav() {
  const [stats, setStats] = useState<JobStats | null>(null);
  const [loadingMock, setLoadingMock] = useState(false);
  const [loadMsg, setLoadMsg] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    function poll() {
      api
        .jobStats()
        .then((s) => !cancelled && setStats(s))
        .catch(() => {});
    }
    poll();
    const id = setInterval(poll, 4000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  async function loadMock() {
    setLoadingMock(true);
    setLoadMsg(null);
    try {
      const result = await api.loadMockData();
      setLoadMsg(
        `${result.sources_created} sources created, ${result.jobs_enqueued} extraction jobs queued.`,
      );
    } catch (e) {
      setLoadMsg(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoadingMock(false);
    }
  }

  return (
    <div className="topnav">
      <span className="brand">Milieu</span>
      <nav>
        <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>
          Entities
        </NavLink>
        <NavLink to="/flows" className={({ isActive }) => (isActive ? "active" : "")}>
          Flows
        </NavLink>
        <NavLink to="/incidents" className={({ isActive }) => (isActive ? "active" : "")}>
          Incidents
        </NavLink>
        <NavLink to="/review" className={({ isActive }) => (isActive ? "active" : "")}>
          Review queue
        </NavLink>
        <NavLink to="/setup" className={({ isActive }) => (isActive ? "active" : "")}>
          Setup
        </NavLink>
      </nav>
      <span className="spacer" />
      <ModeToggle />
      {loadMsg && <span className="job-stats">{loadMsg}</span>}
      <button className="btn small" disabled={loadingMock} onClick={loadMock}>
        {loadingMock ? "Loading…" : "Load mock data"}
      </button>
      {stats && (
        <span className="job-stats">
          jobs: <b>{stats.queued}</b> queued · <b>{stats.running}</b> running · <b>{stats.done}</b> done
          {stats.failed > 0 && (
            <>
              {" "}
              · <b>{stats.failed}</b> failed
            </>
          )}
          {stats.poison > 0 && (
            <>
              {" "}
              · <b>{stats.poison}</b> poison
            </>
          )}
        </span>
      )}
    </div>
  );
}

export default function App() {
  return (
    <div className="app-shell">
      <TopNav />
      <div className="main">
        <Routes>
          <Route path="/" element={<EntityPicker />} />
          <Route path="/entities/:entityId" element={<ContextExplorer />} />
          <Route path="/entities/:entityId/handoffs" element={<HandoffReport />} />
          <Route path="/context/:contextId" element={<ContextStandalone />} />
          <Route path="/review" element={<ReviewQueue />} />
          <Route path="/flows" element={<OrgFlows />} />
          <Route path="/incidents" element={<IncidentPack />} />
          <Route path="/incidents/:incidentId" element={<IncidentPack />} />
          <Route path="/setup" element={<OrgSetup />} />
        </Routes>
      </div>
    </div>
  );
}
