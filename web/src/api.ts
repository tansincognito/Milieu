// Thin fetch wrapper over the backend (§15). No client-side business logic lives here —
// every screen renders exactly what the API returns.

import type {
  ConflictPairOut,
  ConflictResolveRequest,
  ContextHistoryOut,
  ContextLineageOut,
  ContextObjectOut,
  EntityContextOut,
  EntityOut,
  GapOut,
  GapReviewRequest,
  JobStats,
  ReviewRequest,
  SourceOut,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      // ignore — non-JSON error body
    }
    throw new ApiError(resp.status, detail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

function qs(params: Record<string, string | number | undefined | null>): string {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") usp.set(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
}

export const api = {
  listEntities: () => request<EntityOut[]>("/entities"),

  getEntityContext: (entityId: string) =>
    request<EntityContextOut>(`/entities/${entityId}/context`),

  searchContext: (params: { entity?: string; q?: string; type?: string; status?: string; limit?: number }) =>
    request<ContextObjectOut[]>(`/context/search${qs(params)}`),

  getContext: (id: string) => request<ContextObjectOut>(`/context/${id}`),

  getContextHistory: (id: string) => request<ContextHistoryOut>(`/context/${id}/history`),

  getContextLineage: (id: string) => request<ContextLineageOut>(`/context/${id}/lineage`),

  getSource: (id: string) => request<SourceOut>(`/sources/${id}`),

  reviewContext: (id: string, body: ReviewRequest) =>
    request<ContextObjectOut>(`/context/${id}/review`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listConflicts: (entity?: string) =>
    request<ConflictPairOut[]>(`/conflicts${qs({ entity })}`),

  resolveConflict: (relationId: string, body: ConflictResolveRequest) =>
    request<{ relation_id: string; resolution: string }>(`/conflicts/${relationId}/resolve`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listGaps: (params: { entity?: string; status?: string }) =>
    request<GapOut[]>(`/gaps${qs(params)}`),

  reviewGap: (id: string, body: GapReviewRequest) =>
    request<{ gap_id: string; status: string }>(`/gaps/${id}/review`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  loadMockData: () =>
    request<{ sources_created: number; sources_skipped: number; jobs_enqueued: number; consent_rejected: number }>(
      "/sources/mock/load",
      { method: "POST" },
    ),

  jobStats: () => request<JobStats>("/jobs/stats"),
};

export { ApiError };
