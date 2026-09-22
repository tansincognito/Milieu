# Acme Corp — Security & Access Requirements

Owner: Jordan Lee (Account Executive)
Last updated: 2026-10-03

## Requirements

Acme's security team requires SAML-based single sign-on through Okta before they will
roll the product out company-wide. This is a hard requirement, not a preference — their
InfoSec team already runs Okta as the corporate identity provider for every other vendor.

Target date: **December 15, 2026**. Acme's fiscal year closes at the end of December and
their IT freeze starts Dec 20, so SAML/Okta must be live before Dec 15.

SCIM provisioning is also required, not optional: Acme's InfoSec review flagged manual
user provisioning as a compliance gap. They expect user lifecycle (create/deactivate) to
be automated through Okta via SCIM.

Audit logs must be retained for a minimum of 1 year (12 months) to satisfy Acme's SOC 2
audit cycle.

## Priority

This is a P0 blocker for the Acme renewal. Sales cannot close the upsell without a
committed SAML/Okta date.
