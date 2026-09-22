# Technical Design — Acme SSO

Owner: Priya Shah (Engineering Lead)

## Scope

Implement SSO for the Acme account. This covers the SAML 2.0 service-provider flow
against Acme's identity provider.

## Open items

We don't yet have a specific identity provider named for Acme, and there's no committed
ship date in this doc yet — that detail didn't come through in the handoff. Engineering
is treating this as a standard SAML integration until told otherwise.

## Design

- SP-initiated SAML 2.0 flow.
- New `sso_configurations` table keyed by tenant.
- Session handling reuses the existing auth middleware.
