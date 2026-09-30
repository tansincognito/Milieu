# Product Scoping — Globex Field-Level Encryption Request

Owner: Casey Nguyen (Product Manager)
Last updated: 2026-10-20

## Requirement (from Globex, via Sales)

Globex has added a new requirement mid-project: field-level encryption for PII, not just
transport encryption. This came from their security team after the SSO/SCIM review
closed — it wasn't part of the original onboarding scope. Jamie Fox (Globex VP
Engineering) says it's now a condition of go-live, same priority as the original SSO
requirement.

## Open question back to Engineering

Scoping this depends on whether field-level encryption can reuse the existing
at-rest encryption layer or needs a new per-field key management path. Product cannot
give Sales a date to relay to Globex until Engineering answers this — the two paths differ
by what is roughly a sprint versus a quarter of work.

Sales should not commit a date to Globex until this comes back from Engineering.
