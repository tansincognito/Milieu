# Product Decisions — Acme Enterprise Identity

Owner: Sam Rivera (Product Manager)

## Decision: SCIM deferred to Q1

We are deferring SCIM provisioning for Acme to Q1 next year. Rationale: SSO (SAML/Okta)
is the harder deadline and the higher-leverage item for the renewal; building SCIM in
parallel would split engineering focus across the December date. We'll scope SCIM once
SSO ships.

## Decision: audit log retention — 90 days

Our platform default for audit log retention is 90 days, and that's what we're
committing to for the Acme launch. Extending retention further is a separate
infrastructure project we haven't scoped.
