# Technical Constraint — SCIM Provisioning Scale Limit

Owner: Priya Shah (Engineering Lead)
Last updated: 2026-10-18

## Constraint

SCIM provisioning cannot support more than **10,000 users per org** without a redesign of
the sync worker — it currently does a full-org diff on every sync cycle, and that stops
being viable well before 10k. This is a real ceiling, not a soft guideline: we load-tested
to 11k and sync duration goes from ~40s to over 20 minutes.

Acme is at roughly 8,000 provisioned users today and is growing. They will hit this
ceiling within the current contract term at current growth rate.

A redesign (incremental sync instead of full diff) would remove the ceiling, but it's not
scoped or scheduled — it would need to be prioritized against the Q1 SOC 2 work, which is
already the stated top priority.

## What Product needs to do with this

Nobody customer-facing should represent SCIM as supporting unlimited scale until this is
redesigned. If a renewal conversation needs a number, 10,000 is the real ceiling today.
