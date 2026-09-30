#!/usr/bin/env python3
"""One-time backfill: declare the two incidents already in /mock-data as real `incidents`
rows (migration 0009), so the Incident Context Pack reads from real data instead of an
empty table on a fresh environment.

Looks entities up by slug, not a hardcoded UUID -- this dev database has accumulated
duplicate entity rows from earlier concurrent-worker races (multiple "Acme Corp" rows with
different ids), so a hardcoded UUID would silently pick whichever one it was copied from.
Picks the row with the most context objects attached, a reasonable proxy for "the real one"
until that duplication is cleaned up (a separate, already-known issue, not fixed here).

Usage: uv run python ../scripts/declare_seed_incidents.py [--base-url http://localhost:8000]
Idempotent: skips an incident_id that's already declared (backend returns 409).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

INCIDENTS = [
    {
        "incident_id": "INC-1187",
        "title": "API gateway DB pool exhaustion",
        "severity": "P1",
        "entity_slugs": ["globex"],
        "anchor_at": "2026-09-14T10:00:00Z",
        "resolve": True,
    },
    {
        "incident_id": "INC-2311",
        "title": "Checkout API outage",
        "severity": "P0",
        "entity_slugs": ["acme", "globex"],
        "anchor_at": "2026-09-22T19:00:00Z",
        "resolve": False,
    },
]


def _get(url: str) -> object:
    with urllib.request.urlopen(url) as resp:  # noqa: S310 - localhost only
        return json.loads(resp.read())


def _post(url: str, body: dict | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 - localhost only
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _best_entity_id(entities: list[dict], slug: str) -> str | None:
    """The entity with the most source coverage among rows sharing this slug -- see module
    docstring for why there can be more than one."""
    matches = [e for e in entities if e["slug"] == slug]
    if not matches:
        return None
    matches.sort(key=lambda e: sum(e["source_counts"].values()), reverse=True)
    return matches[0]["id"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()

    entities = _get(f"{args.base_url}/entities")
    assert isinstance(entities, list)

    for incident in INCIDENTS:
        entity_ids = []
        for slug in incident["entity_slugs"]:
            entity_id = _best_entity_id(entities, slug)
            if entity_id is None:
                print(f"SKIP {incident['incident_id']}: no entity with slug {slug!r} found")
                break
            entity_ids.append(entity_id)
        else:
            status, body = _post(
                f"{args.base_url}/incidents",
                {
                    "incident_id": incident["incident_id"],
                    "title": incident["title"],
                    "severity": incident["severity"],
                    "entity_ids": entity_ids,
                    "anchor_at": incident["anchor_at"],
                },
            )
            if status == 409:
                print(f"{incident['incident_id']}: already declared, skipping")
            elif status >= 400:
                print(f"{incident['incident_id']}: FAILED ({status}) {body}")
                sys.exit(1)
            else:
                print(f"{incident['incident_id']}: declared, {body['object_count']} confirmed objects")

            if incident["resolve"]:
                rstatus, rbody = _post(f"{args.base_url}/incidents/{incident['incident_id']}/resolve")
                if rstatus < 400:
                    print(f"{incident['incident_id']}: resolved")
                else:
                    print(f"{incident['incident_id']}: resolve FAILED ({rstatus}) {rbody}")


if __name__ == "__main__":
    main()
