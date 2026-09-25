import type { ContextObjectOut } from "../types";

// §5: authority and confidence are never combined into one score. Both render as
// visually distinct badges everywhere an object shows up.

const AUTHORITY_LABELS: Record<number, string> = {
  4: "customer / confirmed",
  3: "owning function",
  2: "sales restating",
  1: "speculative",
  0: "inferred",
};

export function AuthorityBadge({ authority }: { authority: number }) {
  return (
    <span className="authority-badge" title="Authority (§5): how legitimate the claim is organizationally">
      authority {authority} · {AUTHORITY_LABELS[authority] ?? "unknown"}
    </span>
  );
}

export function ConfidenceBadge({ confidence }: { confidence: number }) {
  return (
    <span className="confidence-badge" title="Confidence (§5): how sure the extractor is that the text says this">
      confidence {Math.round(confidence * 100)}%
    </span>
  );
}

export function DueDateInline({ obj }: { obj: ContextObjectOut }) {
  const due = obj.attributes.due_date;
  if (!due) return null;
  const precision = obj.attributes.due_date_precision;
  return (
    <span className="due-date">
      due {due}
      {precision ? ` (${precision})` : ""}
    </span>
  );
}

export function StatusTag({ status }: { status: string }) {
  return <span className={`status-tag ${status}`}>{status}</span>;
}
