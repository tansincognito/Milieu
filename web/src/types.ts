// Mirrors api/app/schemas/context.py and api/app/schemas/extraction.py (§4, §15).
// Kept hand-written and minimal rather than generated, since the API has no OpenAPI
// codegen step wired up yet.

export type ContextType =
  | "requirement"
  | "decision"
  | "constraint"
  | "commitment"
  | "problem"
  | "open_question"
  | "resolution"
  | "dependency";

export type DueDatePrecision = "day" | "month" | "quarter";
export type Priority = "P0" | "P1" | "P2";
export type Stance = "required" | "deferred" | "in_progress" | "done" | "dropped" | "rejected";
export type ContextStatus =
  | "candidate"
  | "active"
  | "conflicting"
  | "superseded"
  | "stale"
  | "ignored";

export interface Impact {
  customers: string[];
  error_rate: number | null;
  data_loss: string | null;
}

export interface TimeWindow {
  start: string;
  end: string;
  timezone: string;
}

export interface SlaImpact {
  breached: boolean | null;
  contract_uptime: number | null;
  credit_owed: boolean | null;
}

export interface ContextAttributes {
  protocol?: string | null;
  idp?: string | null;
  due_date?: string | null;
  due_date_precision?: DueDatePrecision | null;
  priority?: Priority | null;
  stance?: Stance | null;
  acceptance_criteria?: string[] | null;
  rationale?: string | null;
  region?: string | null;
  quantity?: number | null;
  quantity_unit?: string | null;
  integrations?: string[] | null;
  plan?: string | null;
  impact?: Impact | null;
  time_window?: TimeWindow | null;
  root_cause?: string | null;
  sla_impact?: SlaImpact | null;
  remediation?: string | null;
  extra: Record<string, unknown>;
}

export interface ProvenanceOut {
  kind: string;
  stage: string | null;
  source_ts: string;
  provenance: Record<string, unknown> | null;
}

export interface ContextObjectOut {
  id: string;
  tenant_id: string;
  entity_id: string;
  type: ContextType;
  subject_key: string;
  content: string;
  attributes: ContextAttributes;
  actor_label: string;
  actor_role: string;
  stage: string | null;
  authority: number;
  confidence: number;
  status: ContextStatus;
  valid_from: string;
  valid_to: string | null;
  source_id: string;
  evidence_quote: string;
  evidence_span: [number, number];
  version: number;
  created_at: string;
  updated_at: string;
  source: ProvenanceOut | null;
}

export interface ContextVersionOut {
  id: string;
  version: number;
  status: string;
  content: string;
  attributes: Record<string, unknown>;
  changed_by: string | null;
  reason: string | null;
  created_at: string;
}

export interface RelationOut {
  id: string;
  from_id: string;
  to_id: string;
  relation: string;
  created_by: string | null;
  confidence: number | null;
  created_at: string;
  resolved_at: string | null;
}

export interface ContextHistoryOut {
  context_id: string;
  versions: ContextVersionOut[];
  supersession_chain: RelationOut[];
}

export interface LineageNodeOut {
  object_id: string;
  type: string;
  subject_key: string;
  stage: string | null;
  content: string;
  status: string;
  authority: number;
  valid_from: string;
  depth: number;
}

export interface ContextLineageOut {
  context_id: string;
  upstream: LineageNodeOut[];
  downstream: LineageNodeOut[];
}

export interface EntityOut {
  id: string;
  name: string;
  slug: string;
  kind: string;
  source_counts: Record<string, number>;
  open_conflicts: number;
  open_gaps: number;
}

export interface EntityContextGroupOut {
  type: ContextType;
  objects: ContextObjectOut[];
}

export interface EntityContextOut {
  entity: EntityOut;
  current: EntityContextGroupOut[];
  conflicts: ContextObjectOut[];
  source_counts: Record<string, number>;
}

export interface ConflictPairOut {
  relation_id: string;
  from_object: ContextObjectOut;
  to_object: ContextObjectOut;
  created_at: string;
}

export interface SourceOut {
  id: string;
  kind: string;
  stage: string | null;
  source_ts: string;
  text: string | null;
  provenance: Record<string, unknown> | null;
}

export type GapOutcome =
  | "preserved"
  | "equivalent"
  | "generalized"
  | "missing"
  | "contradicted"
  | "object_missing"
  | "stale_reference";

export interface GapOut {
  id: string;
  validation_id: string;
  contract_field: string;
  // Nullable since migration 0004: a present-check violation (e.g. a missing
  // `acceptance_criteria`) has no upstream counterpart to point at.
  upstream_id: string | null;
  downstream_id: string | null;
  slot: string | null;
  outcome: GapOutcome;
  severity: number;
  severity_band: string;
  inherited: boolean;
  upstream_conflict: boolean;
  explanation: string;
  status: string;
  upstream: ContextObjectOut | null;
  downstream: ContextObjectOut | null;
}

export interface ContractOut {
  id: string;
  from_stage: string;
  to_stage: string;
  field_count: number;
}

export interface HandoffValidationOut {
  id: string;
  entity_id: string;
  contract_id: string;
  as_of: string;
  created_at: string;
  summary: {
    total: number;
    by_outcome: Record<string, number>;
    by_severity_band: Record<string, number>;
    inherited: number;
    origin: number;
  };
}

export interface HandoffReportOut extends HandoffValidationOut {
  gaps: GapOut[];
}

export type ReviewAction = "confirm" | "edit" | "ignore" | "mark_stale";

export interface ReviewRequest {
  action: ReviewAction;
  reviewer_id?: string | null;
  note?: string | null;
  content?: string | null;
  attributes?: Record<string, unknown> | null;
}

export type ConflictResolution = "winner" | "both_superseded";

export interface ConflictResolveRequest {
  resolution: ConflictResolution;
  winner_id?: string | null;
  reviewer_id?: string | null;
  note?: string | null;
}

export interface GapReviewRequest {
  action: "confirm" | "ignore";
  reviewer_id?: string | null;
  note?: string | null;
}

export interface JobStats {
  queued: number;
  running: number;
  done: number;
  failed: number;
  poison: number;
}
