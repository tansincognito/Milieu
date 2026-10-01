# Postmortem — INC-1187: API Gateway Database Pool Exhaustion

Owner: Alex Kim (On-call SRE)
Last updated: 2026-09-14

Incident: INC-1187

## Summary

A database connection pool exhaustion on the API gateway caused elevated latency and
intermittent 503s for roughly 22 minutes during business hours. Globex was the only
customer with confirmed customer-visible impact.

## Impact

Globex reported intermittent request failures starting 10:14 IST. Error rate peaked at
7% of requests. No data loss — failed requests were client-retried successfully once the
pool recovered.

## SLA impact

Globex's contract has a 99.9% uptime commitment. This incident's duration and error rate
stayed inside the monthly error budget — no SLA breach, no credit owed.

## Root cause

A batch reporting job introduced the previous week opened connections without releasing
them under a specific retry path, slowly exhausting the gateway's database connection
pool over several hours until it hit the ceiling at 10:14 IST.

## Remediation

Fixed the batch job's connection handling to use a context-managed connection that always
releases, and added a connection pool utilization alert at 80% so this class of issue
pages before it becomes customer-visible next time.
