import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, ApiError } from "./api";
import { ContextExplorer } from "./pages/ContextExplorer";
import { ContextStandalone } from "./pages/ContextStandalone";
import { EntityPicker } from "./pages/EntityPicker";
import { HandoffReport } from "./pages/HandoffReport";
import { ReviewQueue } from "./pages/ReviewQueue";
import type { JobStats } from "./types";

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
        <NavLink to="/review" className={({ isActive }) => (isActive ? "active" : "")}>
          Review queue
        </NavLink>
      </nav>
      <span className="spacer" />
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
        </Routes>
      </div>
    </div>
  );
}
