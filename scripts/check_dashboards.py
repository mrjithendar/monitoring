#!/usr/bin/env python3
"""Static checks for every dashboard in dashboards/ (run by .github/workflows/check.yml and deploy.yml).

    python3 scripts/check_dashboards.py

Each file: valid JSON; uid = file name, lowercase (it names the ConfigMap and every bookmark); id null; unique uid and
title; known folder; panels with unique ids on the 24-column grid without overlapping; only the data sources its org
has (public: Prometheus and Loki; private also Jaeger); every $variable defined; balanced PromQL brackets; links to
/grafana/d/<uid> that exist in the same org; small enough for one ConfigMap. Public dashboards also must not read
jithendar.com's logs (they are private). Queries are not run here: scripts/audit_live.py does that against mk4.
Standard library only.
"""
from __future__ import annotations

import json
import re
import sys

from dashboards import DATASOURCE_VARIABLES, DATASOURCES, DEPLOYED, FOLDER_TAGS, entries, panels

UID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MAX_BYTES = 900 * 1024  # a ConfigMap holds 1 MiB, metadata included
BUILTIN = {"__interval", "__interval_ms", "__range", "__range_s", "__range_ms", "__rate_interval", "__all",
           "__url_time_range", "__value", "__from", "__to", "__dashboard", "__name", "__field", "__series",
           "__data", "__auto", "__span", "__trace", "__org", "__user", "__timezone", "__cell", "__url"}
VARIABLE = re.compile(r"\$\{(\w+)(?:[.:][^}]*)?\}|\$(\w+)|\[\[(\w+)(?::\w+)?\]\]")
LINK = re.compile(r"/grafana/d/([\w-]+)")
GRAFANA = {"-- Grafana --", "grafana"}


def query_texts(target: dict):
    for key in ("expr", "query"):
        value = target.get(key)
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict) and isinstance(value.get("query"), str):
            yield value["query"]


def balanced(expr: str) -> bool:
    pairs, stack = {")": "(", "]": "[", "}": "{"}, []
    for ch in re.sub(r'"(?:[^"\\]|\\.)*"|`[^`]*`', '""', expr):
        if ch in "([{":
            stack.append(ch)
        elif ch in pairs and (not stack or stack.pop() != pairs[ch]):
            return False
    return not stack


def datasource_problem(ds, org: str) -> str | None:
    if ds is None or ds == "":
        return None
    if isinstance(ds, str):
        ds = {"uid": ds}
    uid, kind = ds.get("uid"), ds.get("type")
    if uid in GRAFANA or kind == "grafana" or uid in ("-- Mixed --", "-- Dashboard --"):
        return None
    if uid in DATASOURCE_VARIABLES:
        wanted = DATASOURCE_VARIABLES[uid]
        if kind not in (None, wanted) or wanted not in DATASOURCES[org].values():
            return f"data source variable {uid} of type {kind}"
        return None
    if uid not in DATASOURCES[org] or (kind and DATASOURCES[org][uid] != kind):
        return f"data source {ds} is not in the {org} org"
    return None


def check_one(e, d: dict, problems: list[str]) -> None:
    name = e.rel
    uid, title = d.get("uid"), d.get("title")
    if not uid or not title:
        problems.append(f"{name}: uid and title are required")
        return
    if not UID.match(uid):
        problems.append(f"{name}: uid {uid!r} must be lowercase letters, digits and dashes (it names the ConfigMap)")
    if e.path.stem != uid:
        problems.append(f"{name}: the file name must be the uid ({uid}.json)")
    if d.get("id") is not None:
        problems.append(f"{name}: id must be null (Grafana assigns it)")
    if e.folder and e.folder not in FOLDER_TAGS:
        problems.append(f"{name}: unknown folder {e.folder!r}; add it to FOLDER_TAGS in scripts/dashboards.py")
    if e.path.stat().st_size > MAX_BYTES:
        problems.append(f"{name}: {e.path.stat().st_size} bytes, more than one ConfigMap holds")
    defined = {v["name"] for v in d.get("templating", {}).get("list", [])}

    for v in d.get("templating", {}).get("list", []):
        if v.get("type") == "datasource" and v.get("query") not in DATASOURCES[e.org].values():
            problems.append(f"{name}: variable ${v['name']} selects {v.get('query')} data sources, which the {e.org} org does not have")
        if (p := datasource_problem(v.get("datasource"), e.org)):
            problems.append(f"{name}: variable ${v['name']}: {p}")
    for a in d.get("annotations", {}).get("list", []):
        if (p := datasource_problem(a.get("datasource"), e.org)):
            problems.append(f"{name}: annotation {a.get('name')!r}: {p}")

    ids, cells = set(), {}
    for panel in panels(d):
        pid, label = panel.get("id"), f"{name}: panel {panel.get('id')} {panel.get('title', '')!r}"
        if pid in ids:
            problems.append(f"{label}: duplicate panel id")
        ids.add(pid)
        pos = panel.get("gridPos") or {}
        x, y, w, h = (pos.get(k, 0) for k in ("x", "y", "w", "h"))
        if x < 0 or x + w > 24 or w <= 0 or h <= 0:
            problems.append(f"{label}: outside the 24-column grid ({pos})")
        if panel.get("type") != "row" or not panel.get("collapsed"):
            clash = next(((cx, cy) for cx in range(x, x + w) for cy in range(y, y + h) if (cx, cy) in cells), None)
            if clash:
                problems.append(f"{label}: overlaps panel {cells[clash]}")
            cells.update({(cx, cy): pid for cx in range(x, x + w) for cy in range(y, y + h)})
        for ds in [panel.get("datasource")] + [t.get("datasource") for t in panel.get("targets", [])]:
            if (p := datasource_problem(ds, e.org)):
                problems.append(f"{label}: {p}")
        for t in panel.get("targets", []):
            for text in query_texts(t):
                if not balanced(text):
                    problems.append(f"{label}: unbalanced brackets in {text[:80]!r}")
                if e.org == "public" and re.search(r'namespace\s*=~?\s*"[^"]*jithendar', text):
                    problems.append(f"{label}: reads jithendar.com's logs or metrics by namespace; those dashboards are private")
        texts = [q for t in panel.get("targets", []) for q in query_texts(t)] + [panel.get("title", "")]
        for text in texts:
            for groups in VARIABLE.findall(text):
                var = next(g for g in groups if g)
                if var.isdigit():
                    continue  # $1: a label_replace() back-reference, not a variable
                if var not in defined and var not in BUILTIN:
                    problems.append(f"{label}: uses ${var}, which the dashboard does not define")


def main() -> int:
    problems: list[str] = []
    loaded = []
    for e in entries():
        try:
            d = e.load()
        except json.JSONDecodeError as err:
            problems.append(f"{e.rel}: not valid JSON ({err})")
            continue
        check_one(e, d, problems)
        loaded.append((e, d))

    uids = [d.get("uid") for _, d in loaded]
    titles = [d.get("title") for _, d in loaded]
    for dup in sorted({u for u in uids if uids.count(u) > 1}):
        problems.append(f"uid {dup} is used by more than one dashboard")
    for dup in sorted({t for t in titles if titles.count(t) > 1}):
        problems.append(f"title {dup!r} is used by more than one dashboard")

    # A link opens in the viewer's org: it must name a dashboard deployed to the same one.
    by_org = {org: {d.get("uid") for e, d in loaded if e.org == org} for org in DEPLOYED}
    for e, d in loaded:
        if e.org not in DEPLOYED:
            continue
        for target in sorted(set(LINK.findall(json.dumps(d)))):
            if target not in by_org[e.org]:
                where = next((org for org, u in by_org.items() if target in u), None)
                problems.append(f"{e.rel}: links to /grafana/d/{target}, which is "
                                + (f"in the {where} org, not {e.org}" if where else "no dashboard here"))

    for p in problems:
        print(f"::error::{p}")
    if problems:
        return 1
    counts = {org: sum(1 for e, _ in loaded if e.org == org) for org in ("public", "private", "inactive")}
    print(f"{len(loaded)} dashboards OK: " + ", ".join(f"{n} {org}" for org, n in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
