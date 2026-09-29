"""§14 permission filtering: "Queries filter by `acl && user_principals`." The MVP has no
real auth, so callers pass their principal ids as a repeated `principal` query param;
`["*"]` (mock/public sources) is always included so demo/dev queries work with no
principals supplied.
"""

from __future__ import annotations

from sqlalchemy import ARRAY, String, cast
from sqlalchemy.sql import ColumnElement

from app.models.orm import Sources

PUBLIC_PRINCIPAL = "*"


def acl_visible(principals: list[str] | None) -> ColumnElement[bool]:
    scoped = list({*(principals or []), PUBLIC_PRINCIPAL})
    return Sources.acl.op("&&")(cast(scoped, ARRAY(String)))
