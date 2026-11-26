#!/usr/bin/env python3
"""Gives every hand-written dashboard the shared header (scripts/dashboards.py: standardize) and one JSON layout.

    python3 scripts/format_dashboards.py           # rewrite the files
    python3 scripts/format_dashboards.py --check   # exit 1 when a file would change (CI)

The MOKS and RAG dashboards are not touched here: generators/moks/build.py and generators/rag/build.py write them,
through the same standardize().
Standard library only.
"""
from __future__ import annotations

import sys

from dashboards import DASHBOARDS, dump, entries, standardize

GENERATED = (DASHBOARDS / "public" / "MOKS", DASHBOARDS / "public" / "RAG")


def main(argv: list[str]) -> int:
    check, changed = "--check" in argv, []
    for e in entries():
        if any(e.path.is_relative_to(g) for g in GENERATED):
            continue
        body = dump(standardize(e.load(), e.org, e.folder))
        if e.path.read_text(encoding="utf-8") != body:
            changed.append(e.rel)
            if not check:
                e.path.write_text(body, encoding="utf-8")
    for rel in changed:
        print(f"{'::error::' if check else ''}{rel}{' is not formatted: run python3 scripts/format_dashboards.py' if check else ': formatted'}")
    return 1 if check and changed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
