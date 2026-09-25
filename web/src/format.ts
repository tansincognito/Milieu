// Small display-only helpers. No business logic — just guards against upstream
// extraction occasionally writing the literal string "null" into `actor_label`
// (observed live against real mock data; the pipeline itself is out of scope here).

export function formatActor(label: string | null | undefined): string {
  if (!label || label === "null" || label === "undefined") return "unknown actor";
  return label;
}
