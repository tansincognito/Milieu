import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { ContextHistoryOut, ContextLineageOut, ContextObjectOut, LineageNodeOut, SourceOut } from "../types";
import { AuthorityBadge, ConfidenceBadge, StatusTag } from "./Badges";
import { formatActor } from "../format";

// §17.3 Detail panel: evidence quote highlighted inside the source text, provenance
// fields, authority AND confidence as distinct values, versions, and the lineage chain
// back to root evidence.

function EvidenceBlock({ source, obj }: { source: SourceOut | null; obj: ContextObjectOut }) {
  if (!source) {
    return <div className="empty-state">Loading source…</div>;
  }
  if (source.text === null) {
    // §14 redaction: the caller's principals don't intersect the source ACL.
    return (
      <div className="evidence-block">
        <em>Source text redacted (no permission for this source's ACL).</em>
        <br />
        Evidence quote on record: <mark>{obj.evidence_quote}</mark>
      </div>
    );
  }
  const [start, end] = obj.evidence_span;
  const before = source.text.slice(0, start);
  const quote = source.text.slice(start, end);
  const after = source.text.slice(end);
  const spanMatches = quote === obj.evidence_quote;
  return (
    <div>
      <div className="evidence-block">
        {before}
        <mark>{quote}</mark>
        {after}
      </div>
      {!spanMatches && (
        <div className="banner error" style={{ marginTop: "0.5rem" }}>
          Evidence span does not match `evidence_quote` in the source text as rendered — check
          offsets. (Invariant §4.3: `source.text[evidence_span] == evidence_quote`.)
        </div>
      )}
    </div>
  );
}

function LineageChain({ nodes, direction }: { nodes: LineageNodeOut[]; direction: "upstream" | "downstream" }) {
  if (nodes.length === 0) {
    return <div className="empty-state">No {direction} lineage recorded.</div>;
  }
  const ordered = direction === "upstream" ? [...nodes].sort((a, b) => b.depth - a.depth) : nodes;
  return (
    <div>
      {ordered.map((n) => (
        <div key={n.object_id} className="lineage-item">
          <div>{n.content}</div>
          <div className="lmeta">
            depth {n.depth} · {n.type} · {n.subject_key} · {n.stage ?? "no stage"} · authority {n.authority} ·{" "}
            {n.status}
          </div>
        </div>
      ))}
    </div>
  );
}

export function DetailPanel({
  contextId,
  onClose,
  onNavigate,
  onChanged,
}: {
  contextId: string;
  onClose: () => void;
  onNavigate: (id: string) => void;
  onChanged?: () => void;
}) {
  const [obj, setObj] = useState<ContextObjectOut | null>(null);
  const [source, setSource] = useState<SourceOut | null>(null);
  const [history, setHistory] = useState<ContextHistoryOut | null>(null);
  const [lineage, setLineage] = useState<ContextLineageOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setObj(null);
    setSource(null);
    setHistory(null);
    setLineage(null);
    setError(null);
    setEditing(false);

    api
      .getContext(contextId)
      .then((o) => {
        if (cancelled) return;
        setObj(o);
        setEditContent(o.content);
        api.getSource(o.source_id).then((s) => !cancelled && setSource(s)).catch(() => {});
      })
      .catch((e) => !cancelled && setError(e instanceof ApiError ? e.message : String(e)));

    api.getContextHistory(contextId).then((h) => !cancelled && setHistory(h)).catch(() => {});
    api.getContextLineage(contextId).then((l) => !cancelled && setLineage(l)).catch(() => {});

    return () => {
      cancelled = true;
    };
  }, [contextId]);

  async function doReview(action: "confirm" | "ignore" | "mark_stale") {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.reviewContext(contextId, { action });
      setObj(updated);
      onChanged?.();
      const h = await api.getContextHistory(contextId);
      setHistory(h);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function submitEdit() {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.reviewContext(contextId, { action: "edit", content: editContent });
      setObj(updated);
      setEditing(false);
      onChanged?.();
      const h = await api.getContextHistory(contextId);
      setHistory(h);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-header">
          <div>
            <h2>{obj ? obj.subject_key : "Loading…"}</h2>
            {obj && (
              <div className="meta">
                <StatusTag status={obj.status} /> · v{obj.version} · {obj.type}
              </div>
            )}
          </div>
          <button className="btn small" onClick={onClose}>
            Close
          </button>
        </div>

        {error && <div className="banner error">{error}</div>}

        {!obj && !error && <div className="empty-state">Loading…</div>}

        {obj && (
          <>
            <div className="drawer-section">
              <h4>Content</h4>
              <p>{obj.content}</p>
            </div>

            <div className="drawer-section">
              <h4>Authority &amp; confidence (never blended)</h4>
              <div className="score-row">
                <div className="score-box">
                  <div className="label">Authority</div>
                  <div className="value">{obj.authority} / 4</div>
                  <div className="note">
                    <AuthorityBadge authority={obj.authority} />
                  </div>
                </div>
                <div className="score-box">
                  <div className="label">Confidence</div>
                  <div className="value">{Math.round(obj.confidence * 100)}%</div>
                  <div className="note">
                    <ConfidenceBadge confidence={obj.confidence} />
                  </div>
                </div>
              </div>
            </div>

            <div className="drawer-section">
              <h4>Evidence (highlighted in source)</h4>
              <EvidenceBlock source={source} obj={obj} />
            </div>

            <div className="drawer-section">
              <h4>Provenance</h4>
              <table className="kv-table">
                <tbody>
                  <tr>
                    <td>Actor</td>
                    <td>
                      {formatActor(obj.actor_label)} ({obj.actor_role})
                    </td>
                  </tr>
                  <tr>
                    <td>Stage</td>
                    <td>{obj.stage ?? "—"}</td>
                  </tr>
                  <tr>
                    <td>Source kind</td>
                    <td>{obj.source?.kind ?? "—"}</td>
                  </tr>
                  <tr>
                    <td>Source timestamp</td>
                    <td>{obj.source ? new Date(obj.source.source_ts).toLocaleString() : "—"}</td>
                  </tr>
                  <tr>
                    <td>Source detail</td>
                    <td>
                      {obj.source?.provenance ? (
                        <pre style={{ margin: 0, fontSize: "0.78rem", whiteSpace: "pre-wrap" }}>
                          {JSON.stringify(obj.source.provenance, null, 2)}
                        </pre>
                      ) : (
                        "redacted or unavailable"
                      )}
                    </td>
                  </tr>
                  <tr>
                    <td>Valid from</td>
                    <td>{new Date(obj.valid_from).toLocaleString()}</td>
                  </tr>
                  <tr>
                    <td>Valid to</td>
                    <td>{obj.valid_to ? new Date(obj.valid_to).toLocaleString() : "still active"}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div className="drawer-section">
              <h4>Versions</h4>
              {history && history.versions.length > 0 ? (
                history.versions.map((v) => (
                  <div key={v.id} className="version-item">
                    <div>
                      v{v.version} → <StatusTag status={v.status} /> — {v.content}
                    </div>
                    <div className="vmeta">
                      {v.reason ?? "no reason recorded"} · {new Date(v.created_at).toLocaleString()}
                    </div>
                  </div>
                ))
              ) : (
                <div className="empty-state">No versions beyond the original extraction yet.</div>
              )}
            </div>

            <div className="drawer-section">
              <h4>Lineage chain (back to root evidence)</h4>
              <div className="lineage-chain-label">Upstream (further back in the chain)</div>
              {lineage ? <LineageChain nodes={lineage.upstream} direction="upstream" /> : <div className="empty-state">Loading…</div>}
              <div className="lineage-item current">
                <div>{obj.content}</div>
                <div className="lmeta">this object · {obj.subject_key}</div>
              </div>
              <div className="lineage-chain-label">Downstream</div>
              {lineage ? <LineageChain nodes={lineage.downstream} direction="downstream" /> : <div className="empty-state">Loading…</div>}
            </div>

            <div className="drawer-section">
              <h4>Review actions</h4>
              {!editing ? (
                <div className="review-actions">
                  <button className="btn primary" disabled={busy} onClick={() => doReview("confirm")}>
                    Confirm
                  </button>
                  <button className="btn" disabled={busy} onClick={() => setEditing(true)}>
                    Edit
                  </button>
                  <button className="btn" disabled={busy} onClick={() => doReview("ignore")}>
                    Ignore
                  </button>
                  <button className="btn danger" disabled={busy} onClick={() => doReview("mark_stale")}>
                    Mark Stale
                  </button>
                  <button className="btn small" onClick={() => onNavigate(obj.id)}>
                    Open standalone
                  </button>
                </div>
              ) : (
                <div className="edit-form">
                  <textarea
                    rows={3}
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                  />
                  <div className="review-actions">
                    <button className="btn primary" disabled={busy} onClick={submitEdit}>
                      Save new version
                    </button>
                    <button className="btn" disabled={busy} onClick={() => setEditing(false)}>
                      Cancel
                    </button>
                  </div>
                  <div className="empty-state" style={{ padding: "0.4rem 0", textAlign: "left" }}>
                    Editing creates a new version (§4.3). Evidence quote and source stay untouched.
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
