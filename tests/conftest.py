import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dashboards  # noqa: E402


def exprs(board: dict, kind: str = "prometheus") -> list[str]:
    """The queries of every panel that reads data source type `kind` (panel or target level, variables resolved)."""
    out = []
    for p in dashboards.panels(board):
        for t in p.get("targets", []):
            ds = t.get("datasource") or p.get("datasource") or {}
            uid = ds.get("uid", "") if isinstance(ds, dict) else ds
            t_kind = dashboards.DATASOURCE_VARIABLES.get(uid) or (ds.get("type") if isinstance(ds, dict) else None)
            if t_kind == kind and isinstance(t.get("expr"), str):
                out.append(t["expr"])
    return out


@pytest.fixture(scope="session")
def boards() -> dict[str, dict]:
    """uid → dashboard, every org."""
    return {e.path.stem: e.load() for e in dashboards.entries()}


@pytest.fixture(scope="session")
def board(boards):
    return lambda uid: boards[uid]
