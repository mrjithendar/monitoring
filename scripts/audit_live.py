#!/usr/bin/env python3
"""Runs every dashboard query against the live Prometheus and Loki and reports panels that would show nothing.

    kubectl -n monitoring port-forward deploy/prometheus 19090:9090 &
    kubectl -n monitoring port-forward svc/loki 13100:3100 &
    python3 scripts/audit_live.py                  # every dashboard
    python3 scripts/audit_live.py platform-redis   # dashboards whose uid contains this
    python3 scripts/audit_live.py --json           # machine-readable

For each query: an ERROR (the data source refused it), MISSING (it reads a metric Prometheus has never stored), or
EMPTY (no series over the window). EMPTY is not always a fault: a panel of errors is empty while nothing fails.
Variables take their "All" value (or the first value Prometheus has for them), and Grafana's macros fixed stand-ins.
Not run in CI: GitHub's runners cannot reach the cluster's Prometheus. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROM = os.environ.get("PROM_URL", "http://localhost:19090/prometheus")
LOKI = os.environ.get("LOKI_URL", "http://localhost:13100")
WINDOW_S = int(os.environ.get("AUDIT_WINDOW_S", str(6 * 3600)))
MACROS = {"__rate_interval": "2m", "__interval": "1m", "__interval_ms": "60000", "__range": "6h", "__range_s": "21600",
          "__range_ms": "21600000", "__auto": "1m"}
VARIABLE = re.compile(r"\$\{(\w+)(?::[^}]*)?\}|\$(\w+)|\[\[(\w+)(?::\w+)?\]\]")
# PromQL words that look like metric names but are not.
KEYWORDS = {"by", "without", "on", "ignoring", "group_left", "group_right", "bool", "and", "or", "unless", "offset",
            "inf", "nan", "atan2", "topk", "bottomk", "sum", "min", "max", "avg", "count", "stddev", "stdvar", "quantile",
            "count_values", "group", "limitk", "limit_ratio"}
NAME = re.compile(r"(?<![\w:\"'.$])([a-zA-Z_:][a-zA-Z0-9_:]*)\s*(?=\{|\[|\)|\s|$|,|>|<|=|\*|/|\+|-)")


def get(url: str, params: dict[str, str]) -> dict:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(full, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            return json.loads(body)
        except ValueError:
            return {"status": "error", "error": f"HTTP {e.code}: {body[:200]}"}
    except (urllib.error.URLError, TimeoutError) as e:
        return {"status": "error", "error": f"unreachable: {e}"}


def dashboards(filter_: str | None):
    for path in sorted((ROOT / "dashboards").rglob("*.json")):
        d = json.loads(path.read_text())
        if filter_ and filter_ not in d["uid"]:
            continue
        yield path, d


def panels(d: dict):
    for p in d.get("panels", []):
        yield p
        yield from p.get("panels", [])


def kind_of(ds, panel_ds) -> str | None:
    ds = ds or panel_ds
    if isinstance(ds, dict):
        t, uid = ds.get("type"), ds.get("uid", "")
        if uid in ("${datasource}", "$datasource"):
            return "prometheus"
        if uid in ("${logs}", "$logs"):
            return "loki"
        return t
    if isinstance(ds, str):
        return {"roboshop-prometheus": "prometheus", "roboshop-loki": "loki", "jaeger": "jaeger"}.get(ds, ds)
    return None


class Values:
    """A value for each dashboard variable: its All value, or the first one Prometheus has."""

    def __init__(self, d: dict, names: set[str]):
        self.values: dict[str, str] = dict(MACROS)
        for v in d.get("templating", {}).get("list", []):
            self.values[v["name"]] = self._value(v, names)

    def _value(self, v: dict, names: set[str]) -> str:
        t = v.get("type")
        if t == "constant":
            return str(v.get("query", ""))
        if t == "datasource":
            return "roboshop-prometheus" if v.get("query") == "prometheus" else "roboshop-loki"
        if t in ("textbox", "adhoc"):
            current = v.get("current", {}).get("value") if isinstance(v.get("current"), dict) else None
            return str(current or v.get("query") or "")
        if v.get("includeAll"):
            return v.get("allValue") or ".*"
        if t == "custom":
            first = (v.get("query") or "").split(",")[0].strip()
            return first.split(" : ")[-1]
        if t == "interval":
            return (v.get("query") or "1m").split(",")[0]
        if t == "query":
            definition = v.get("definition") or (v.get("query") or {}).get("query", "") if isinstance(v.get("query"), dict) else v.get("definition") or v.get("query") or ""
            m = re.match(r"label_values\((?:(.*),\s*)?(\w+)\)", definition.strip())
            if m:
                params = {"match[]": m.group(1)} if m.group(1) else {}
                params.update(start=str(time.time() - WINDOW_S), end=str(time.time()))
                values = get(f"{PROM}/api/v1/label/{m.group(2)}/values", params).get("data") or []
                if values:
                    return values[0]
        return ".*"

    def apply(self, text: str) -> str:
        def sub(m: re.Match) -> str:
            name = m.group(1) or m.group(2) or m.group(3)
            return self.values.get(name, m.group(0))
        return VARIABLE.sub(sub, text)


def metric_names(expr: str) -> set[str]:
    expr = re.sub(r'"(?:[^"\\]|\\.)*"', '""', expr)          # strings
    expr = re.sub(r"\{[^}]*\}", "{}", expr)                   # matchers
    expr = re.sub(r"\[[^\]]*\]", "[]", expr)                  # ranges
    # Label lists: sum by (pod), on (instance) group_left (node), without (le).
    expr = re.sub(r"\b(by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", " ", expr)
    names = set()
    for m in NAME.finditer(expr):
        word = m.group(1)
        after = expr[m.end():m.end() + 1]
        if word in KEYWORDS or after == "(":
            continue
        if re.match(r"^\d", word) or word in ("e",):
            continue
        names.add(word)
    return names


def check_prometheus(expr: str, names: set[str]) -> tuple[str, str]:
    missing = sorted(n for n in metric_names(expr) if n not in names)
    now = time.time()
    r = get(f"{PROM}/api/v1/query_range", {"query": expr, "start": str(now - WINDOW_S), "end": str(now), "step": "300"})
    if r.get("status") != "success":
        return "ERROR", r.get("error", "")[:200]
    if missing:
        return "MISSING", ", ".join(missing)
    if not r["data"]["result"]:
        return "EMPTY", ""
    return "OK", ""


def check_loki(expr: str) -> tuple[str, str]:
    now = time.time()
    r = get(f"{LOKI}/loki/api/v1/query_range", {"query": expr, "start": str(int((now - WINDOW_S) * 1e9)),
                                                 "end": str(int(now * 1e9)), "limit": "1", "step": "300"})
    if r.get("status") != "success":
        return "ERROR", (r.get("error") or r.get("message") or str(r))[:200]
    return ("OK", "") if r["data"]["result"] else ("EMPTY", "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("filter", nargs="?")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    names_r = get(f"{PROM}/api/v1/label/__name__/values", {})
    if names_r.get("status") != "success":
        print(f"Prometheus at {PROM}: {names_r.get('error')}", file=sys.stderr)
        return 2
    names = set(names_r["data"])
    report = []
    for path, d in dashboards(args.filter):
        values = Values(d, names)
        for p in panels(d):
            for t in p.get("targets", []):
                kind = kind_of(t.get("datasource"), p.get("datasource"))
                raw = t.get("expr") or (t.get("query") if isinstance(t.get("query"), str) else "")
                if not raw or kind not in ("prometheus", "loki"):
                    continue
                expr = values.apply(raw)
                status, detail = check_prometheus(expr, names) if kind == "prometheus" else check_loki(expr)
                report.append({"dashboard": d["uid"], "panel": p.get("title", ""), "ref": t.get("refId", ""),
                               "kind": kind, "status": status, "detail": detail, "expr": raw})
    if args.json:
        json.dump(report, sys.stdout, indent=1)
        return 0
    bad = [r for r in report if r["status"] != "OK"]
    for r in bad:
        print(f"{r['status']:8} {r['dashboard']} / {r['panel']} [{r['ref']}] {r['detail']}")
    counts = {s: sum(1 for r in report if r["status"] == s) for s in ("OK", "EMPTY", "MISSING", "ERROR")}
    print(f"\n{len(report)} queries: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return 1 if counts["ERROR"] or counts["MISSING"] else 0


if __name__ == "__main__":
    sys.exit(main())
