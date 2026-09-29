// §17.4 — the handoff report. A slot x stage grid per subject showing what happened to
// each piece of context as it moved between stages, and whether a loss started here
// (origin) or was already lost upstream (inherited).
//
// The grid is built entirely from GapOut rows. A cell with no gap is `preserved`: the
// validator only writes rows for losses (§10.1 point 4 — preserved and equivalent produce
// no gap), so "no row" is meaningful data, not missing data.

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError } from "./../api";
import { DetailPanel } from "../components/DetailPanel";
import type { GapOut, GapOutcome, HandoffReportOut } from "../types";

const OUTCOME_MARK: Record<GapOutcome | "preserved", string> = {
  preserved: "✓",
  equivalent: "✓",
  generalized: "~",
  missing: "✗",
  contradicted: "!",
  object_missing: "∅",
  stale_reference: "⟲",
};

const OUTCOME_LABEL: Record<string, string> = {
  preserved: "Preserved",
  equivalent: "Equivalent wording",
  generalized: "Generalized",
  missing: "Missing",
  contradicted: "Contradicted",
  object_missing: "No downstream object",
  stale_reference: "Stale reference",
};

function cellClass(outcome: string, inherited: boolean): string {
  return `hcell hcell-${outcome}${inherited ? " hcell-inherited" : ""}`;
}

/** Group a contract's gaps into rows (one per slot) keyed by the subject they belong to. */
function groupBySubject(gaps: GapOut[]): Map<string, GapOut[]> {
  const bySubject = new Map<string, GapOut[]>();
  for (const gap of gaps) {
    // Prefer the upstream object's subject; a present-check gap has no upstream, so fall
    // back to the downstream side, then to the contract field name.
    const subject =
      gap.upstream?.subject_key ?? gap.downstream?.subject_key ?? gap.contract_field;
    const existing = bySubject.get(subject);
    if (existing) existing.push(gap);
    else bySubject.set(subject, [gap]);
  }
  return bySubject;
}

function GapCell({ gap, onOpen }: { gap: GapOut; onOpen: (id: string) => void }) {
  const target = gap.downstream_id ?? gap.upstream_id;
  return (
    <td
      className={cellClass(gap.outcome, gap.inherited)}
      title={`${OUTCOME_LABEL[gap.outcome] ?? gap.outcome} · severity ${gap.severity.toFixed(2)} (${
        gap.severity_band
      }) · ${gap.inherited ? "inherited from an earlier handoff" : "originates here"}\n${
        gap.explanation
      }`}
    >
      <button
        type="button"
        className="hcell-btn"
        disabled={!target}
        onClick={() => target && onOpen(target)}
      >
        <span className="hmark">{OUTCOME_MARK[gap.outcome as GapOutcome] ?? "?"}</span>
        <span className="hsev">{gap.severity.toFixed(1)}</span>
      </button>
      {gap.inherited && <span className="hbadge-inherited">inherited</span>}
      {gap.upstream_conflict && <span className="hbadge-conflict">upstream conflict</span>}
    </td>
  );
}

function ContractGrid({
  report,
  onOpen,
}: {
  report: HandoffReportOut;
  onOpen: (id: string) => void;
}) {
  const bySubject = useMemo(() => groupBySubject(report.gaps), [report.gaps]);
  const summary = report.summary;

  return (
    <section className="hcontract">
      <header className="hcontract-head">
        <h3>{report.contract_id.replace(/_/g, " ")}</h3>
        <span className="hsummary">
          {summary.total === 0 ? (
            <b className="ok">no losses</b>
          ) : (
            <>
              <b>{summary.total}</b> gap{summary.total === 1 ? "" : "s"} · {summary.origin} origin ·{" "}
              {summary.inherited} inherited
            </>
          )}
        </span>
      </header>

      {summary.total === 0 ? (
        <p className="muted">
          Every slot this contract checks survived the handoff intact.
        </p>
      ) : (
        <table className="hgrid">
          <thead>
            <tr>
              <th>Subject</th>
              <th>Slot</th>
              <th>Outcome</th>
              <th>What happened</th>
            </tr>
          </thead>
          <tbody>
            {[...bySubject.entries()].map(([subject, gaps]) =>
              gaps.map((gap, i) => (
                <tr key={gap.id}>
                  {i === 0 && (
                    <td rowSpan={gaps.length} className="hsubject">
                      {subject}
                    </td>
                  )}
                  <td className="hslot">{gap.slot ?? gap.contract_field}</td>
                  <GapCell gap={gap} onOpen={onOpen} />
                  <td className="hexplain">{gap.explanation}</td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function HandoffReport() {
  const { entityId } = useParams<{ entityId: string }>();
  const [reports, setReports] = useState<HandoffReportOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!entityId) return;
    try {
      setReports(await api.listEntityHandoffs(entityId));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, [entityId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runValidation() {
    if (!entityId) return;
    setRunning(true);
    setError(null);
    try {
      await api.validateAllHandoffs(entityId);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  const totalGaps = reports?.reduce((n, r) => n + r.summary.total, 0) ?? 0;

  return (
    <div className="handoff-page">
      <header className="page-head">
        <h2>Handoff report</h2>
        <span className="spacer" />
        {reports && reports.length > 0 && (
          <span className="job-stats">
            {totalGaps} gap{totalGaps === 1 ? "" : "s"} across {reports.length} handoff
            {reports.length === 1 ? "" : "s"}
          </span>
        )}
        <button className="btn small" disabled={running} onClick={runValidation}>
          {running ? "Validating…" : "Run validation"}
        </button>
      </header>

      <p className="legend">
        <span className="hcell-generalized">~</span> generalized{" "}
        <span className="hcell-missing">✗</span> missing{" "}
        <span className="hcell-contradicted">!</span> contradicted{" "}
        <span className="hcell-object_missing">∅</span> no downstream object{" "}
        <span className="hcell-stale_reference">⟲</span> stale reference · the number is
        severity · <b>inherited</b> means the loss already happened at an earlier handoff
      </p>

      {error && <div className="error">{error}</div>}

      {reports === null && !error && <p className="muted">Loading…</p>}

      {reports !== null && reports.length === 0 && (
        <p className="muted">
          No handoffs have been validated for this entity yet. Press <b>Run validation</b> to
          check every contract against the context extracted so far.
        </p>
      )}

      {reports?.map((report) => (
        <ContractGrid key={report.id} report={report} onOpen={setOpenId} />
      ))}

      {openId && (
        <DetailPanel
          contextId={openId}
          onClose={() => setOpenId(null)}
          onNavigate={setOpenId}
          onChanged={load}
        />
      )}
    </div>
  );
}
