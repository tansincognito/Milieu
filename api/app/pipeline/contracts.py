"""Context Contracts (§9): typed slot checks, stored as data in `context_contracts` and
seeded from YAML in `/contracts`. Two check kinds per §9:

- `propagate`: every qualifying upstream object must have a downstream counterpart (same
  `subject_key`), and the listed slots must survive.
- `present`: every downstream object of the given type must satisfy `rule` (a lineage
  check) or have the listed slots set, with no upstream comparison needed.

`context_contracts.to_stage` (§16) is a single string column. One YAML file
(`engineering_to_customer_facing.yaml`) declares `to_stage: [sales, customer_success]`
(§9's own example), so the loader expands a list `to_stage` into one DB row per stage,
suffixing the id (`<base_id>::<stage>`) so each is independently addressable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.models.orm import ContextContracts

CheckKind = Literal["propagate", "present"]
Importance = Literal["critical", "high", "normal"]


class ContractField(BaseModel):
    name: str
    check: CheckKind
    types: list[str]
    slots: list[str] = Field(default_factory=list)
    importance: Importance = "normal"
    min_upstream_authority: int = 0
    rule: str | None = None  # only meaningful when check == "present"


class ContractSpec(BaseModel):
    """A single (from_stage, to_stage) contract, already resolved to one concrete
    `to_stage` — see module docstring for how a list `to_stage` in YAML is expanded."""

    id: str
    from_stage: str
    to_stage: str
    fields: list[ContractField]


def _parse_yaml_file(path: Path) -> list[ContractSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    base_id = raw["id"]
    from_stage = raw["from_stage"]
    to_stage_raw = raw["to_stage"]
    fields = [ContractField.model_validate(f) for f in raw["fields"]]

    to_stages = to_stage_raw if isinstance(to_stage_raw, list) else [to_stage_raw]
    specs: list[ContractSpec] = []
    for to_stage in to_stages:
        contract_id = base_id if len(to_stages) == 1 else f"{base_id}::{to_stage}"
        specs.append(
            ContractSpec(id=contract_id, from_stage=from_stage, to_stage=to_stage, fields=fields)
        )
    return specs


def load_contract_files(contracts_dir: Path) -> list[ContractSpec]:
    """Parses every `*.yaml` file in `contracts_dir` into `ContractSpec`s. Pure — no DB."""
    specs: list[ContractSpec] = []
    for path in sorted(contracts_dir.glob("*.yaml")):
        specs.extend(_parse_yaml_file(path))
    return specs


def load_contracts(db: Session, contracts_dir: Path) -> list[str]:
    """Upserts every contract in `contracts_dir` into `context_contracts` (§16). Idempotent
    and safe to re-run — matches the pattern of `seed_directory.load_directory`. Returns the
    list of contract ids loaded."""
    specs = load_contract_files(contracts_dir)
    ids: list[str] = []
    for spec in specs:
        row = db.get(ContextContracts, spec.id)
        spec_json = spec.model_dump(mode="json")
        if row is None:
            db.add(
                ContextContracts(
                    id=spec.id,
                    from_stage=spec.from_stage,
                    to_stage=spec.to_stage,
                    spec=spec_json,
                    version=1,
                )
            )
        else:
            row.from_stage = spec.from_stage
            row.to_stage = spec.to_stage
            row.spec = spec_json
            row.version += 1
        ids.append(spec.id)
    db.commit()
    return ids


def get_contract(db: Session, contract_id: str) -> ContractSpec | None:
    row = db.get(ContextContracts, contract_id)
    if row is None:
        return None
    return ContractSpec.model_validate(row.spec)
