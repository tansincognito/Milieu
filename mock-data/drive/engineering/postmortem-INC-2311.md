# Postmortem — INC-2311: Checkout API outage

Owner: Priya Shah (Engineering Lead)
Incident: INC-2311
Date: 2026-11-27

## Summary

Checkout API served elevated 5xx errors during peak evening traffic. Root cause was AWS
us-east-1 ELB degradation, not our 18:00 deploy (which was initially suspected and rolled
back with no effect).

## Impact

- Window: **18:05–18:52 IST** (peak hours), 47 minutes.
- 12% of requests failed during the window (error_rate 0.12).
- Customers affected: **Acme Corp** and **Globex Corporation**.
- ~300 writes were queued during the outage and have since been replayed successfully —
  no permanent data loss, but ~300 writes were delayed.

## SLA impact

Acme's contract guarantees 99.95% monthly uptime. This incident breaches that SLA for the
current billing month. A service credit is owed per the contract terms.

## Root cause

AWS us-east-1 ELB degradation. (Initial on-call suspicion was our 18:00 deploy — that was
ruled out after rollback showed no change.)

## Remediation

Multi-AZ failover for the checkout API, targeted for **December 15, 2026**, so a
single-AZ ELB degradation can no longer take down checkout.
