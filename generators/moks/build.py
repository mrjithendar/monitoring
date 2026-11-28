#!/usr/bin/env python3
"""Builds the MOKS Grafana dashboards in dashboards/public/MOKS/ (org Public, folder MOKS).

    python3 generators/moks/build.py           # write the JSON files
    python3 generators/moks/build.py --check   # exit 1 when a file differs from what this script builds (CI)

Edit the dashboards here, not in the JSON: tests/test_moks_build.py fails when the files and this script disagree.
Moved from mrjithendar/moks-sre-agent (deploy/grafana/build.py) on 2026-11-24; the metrics they read are defined there
(backend/moks/observability.py, reranker/app.py). Whether each one has data on mk4: scripts/audit_live.py.

Queries select job="moks" (moks-sre-agent: deploy/grafana/prometheus-scrape.yaml). Panels that split by process use `instance` and the
`component` variable, which needs the pod-discovery scrape job: with the DNS job every series has only a pod IP, and
`component=~".*"` still matches. Standard library only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from dashboards import dump, standardize  # noqa: E402

OUT = ROOT / "dashboards" / "public" / "MOKS"
PROM = {"type": "prometheus", "uid": "${datasource}"}
LOKI = {"type": "loki", "uid": "${logs}"}
J = 'job="moks"'
PROCESSES = 'job="moks", component=~"$component"'
APP_CONTAINERS = 'container=~"api|worker|crewai|executor|terminal|reranker|embedder"'


# ---- panels -------------------------------------------------------------------------------------------------------

def q(expr: str, legend: str = "", *, instant: bool = False, ref: str | None = None) -> dict[str, Any]:
    t: dict[str, Any] = {"datasource": PROM, "expr": expr, "legendFormat": legend or "__auto", "refId": ref or ""}
    if instant:
        t.update(instant=True, range=False, format="time_series")
    return t


def _thresholds(steps: list[tuple[str, float | None]] | None) -> dict[str, Any]:
    steps = steps or [("green", None)]
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in steps]}


def stat(title: str, expr: str, desc: str, *, unit: str = "short", steps: list[tuple[str, float | None]] | None = None, decimals: int | None = None,
         no_value: str = "0", graph: bool = False, w: int = 4, h: int = 4, mappings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    defaults: dict[str, Any] = {"unit": unit, "thresholds": _thresholds(steps), "color": {"mode": "thresholds"}, "noValue": no_value}
    if decimals is not None:
        defaults["decimals"] = decimals
    if mappings:
        defaults["mappings"] = mappings
    return {"type": "stat", "title": title, "description": desc, "datasource": PROM, "_w": w, "_h": h,
            "targets": [q(expr, instant=not graph)],
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "value",
                        "graphMode": "area" if graph else "none", "justifyMode": "auto", "textMode": "value", "orientation": "auto", "wideLayout": True}}


def ts(title: str, targets: list[dict[str, Any]], desc: str, *, unit: str = "short", w: int = 8, h: int = 8, bars: bool = False, stack: bool = False,
       interval: str | None = None, calcs: list[str] | None = None, points: bool = False, overrides: list[dict[str, Any]] | None = None,
       max_: float | None = None) -> dict[str, Any]:
    custom: dict[str, Any] = {"drawStyle": "bars" if bars else "line", "lineWidth": 1, "fillOpacity": 70 if bars else 12,
                              "gradientMode": "none", "showPoints": "always" if points else "never", "pointSize": 5,
                              "spanNulls": False, "stacking": {"mode": "normal" if stack or bars else "none", "group": "A"},
                              "axisSoftMin": 0, "barAlignment": 0}
    defaults: dict[str, Any] = {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"}}
    if max_ is not None:
        defaults["max"] = max_
    p = {"type": "timeseries", "title": title, "description": desc, "datasource": PROM, "_w": w, "_h": h, "targets": targets,
         "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
         "options": {"legend": {"displayMode": "table" if calcs else "list", "placement": "bottom", "showLegend": True, "calcs": calcs or []},
                     "tooltip": {"mode": "multi", "sort": "desc"}}}
    if interval:
        p["interval"] = interval
    return p


def bar(title: str, targets: list[dict[str, Any]], desc: str, *, unit: str = "short", w: int = 8, h: int = 8, decimals: int | None = None,
        steps: list[tuple[str, float | None]] | None = None) -> dict[str, Any]:
    """Horizontal bars over the dashboard's whole time range (instant queries, usually increase(...[$__range]))."""
    defaults: dict[str, Any] = {"unit": unit, "min": 0, "thresholds": _thresholds(steps), "color": {"mode": "thresholds" if steps else "continuous-BlYlRd"}}
    if decimals is not None:
        defaults["decimals"] = decimals
    return {"type": "bargauge", "title": title, "description": desc, "datasource": PROM, "_w": w, "_h": h,
            "targets": [{**t, "instant": True, "range": False, "format": "time_series"} for t in targets],
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True, "valueMode": "color", "namePlacement": "auto",
                        "sizing": "auto", "minVizHeight": 10, "maxVizHeight": 300,
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}}


def logs(title: str, expr: str, desc: str, *, h: int = 10) -> dict[str, Any]:
    return {"type": "logs", "title": title, "description": desc, "datasource": LOKI, "_w": 24, "_h": h,
            "targets": [{"datasource": LOKI, "expr": expr, "refId": "A", "queryType": "range"}],
            "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True, "dedupStrategy": "none",
                        "prettifyLogMessage": False, "showCommonLabels": False, "showLabels": False}}


def text(content: str, *, w: int = 24, h: int = 3) -> dict[str, Any]:
    return {"type": "text", "title": "", "_w": w, "_h": h, "options": {"mode": "markdown", "content": content}}


def row(title: str) -> dict[str, Any]:
    return {"type": "row", "title": title, "collapsed": False, "panels": [], "_w": 24, "_h": 1}


def layout(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Left to right, wrapping at 24 columns; a row starts on a new line. Ids follow the order."""
    out, x, y, line_h = [], 0, 0, 0
    for n, p in enumerate(items, start=1):
        w, h = p.pop("_w"), p.pop("_h")
        if p["type"] == "row" or x + w > 24:
            y, x, line_h = y + line_h, 0, 0
        p["id"] = n
        p["gridPos"] = {"h": h, "w": w, "x": x, "y": y}
        for i, t in enumerate(p.get("targets", [])):
            t["refId"] = t.get("refId") or chr(ord("A") + i)
        out.append(p)
        x += w
        line_h = max(line_h, h)
        if p["type"] == "row":
            y, x, line_h = y + 1, 0, 0
    return out


# ---- dashboards ---------------------------------------------------------------------------------------------------

VARIABLES = [
    {"name": "datasource", "label": "Metrics", "type": "datasource", "query": "prometheus", "hide": 0, "current": {}, "regex": "", "refresh": 1},
    {"name": "logs", "label": "Logs", "type": "datasource", "query": "loki", "hide": 0, "current": {}, "regex": "", "refresh": 1},
    {"name": "namespace", "label": "Namespace (logs)", "type": "constant", "query": "moks-sre", "hide": 2},
]
COMPONENT = {"name": "component", "label": "Component", "type": "query", "datasource": PROM, "hide": 0,
             "definition": 'label_values(up{job="moks"}, component)', "query": {"qryType": 1, "query": 'label_values(up{job="moks"}, component)', "refId": "PrometheusVariableQueryEditor-VariableQuery"},
             "refresh": 2, "includeAll": True, "multi": True, "allValue": ".*", "sort": 1, "current": {"text": ["All"], "value": ["$__all"]}, "options": [], "regex": ""}

ANNOTATIONS = [
    {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
     "name": "Annotations & Alerts", "type": "dashboard"},
    {"name": "MOKS processes started", "datasource": PROM, "enable": True, "hide": False, "iconColor": "purple", "step": "1m",
     "expr": f"count(time() - process_start_time_seconds{{{J}}} < 90) > 0", "titleFormat": "MOKS processes started (rollout or restart)",
     "textFormat": "{{value}} process(es)", "useValueForTime": False},
]


def dashboard(uid: str, title: str, desc: str, panels: list[dict[str, Any]], *, tags: list[str], time_from: str = "now-24h",
              component: bool = False) -> dict[str, Any]:
    return {
        "uid": uid, "title": title, "description": desc, "tags": ["moks", *tags], "editable": False, "graphTooltip": 1, "timezone": "browser",
        "time": {"from": time_from, "to": "now"}, "refresh": "1m", "schemaVersion": 41, "version": 1, "fiscalYearStartMonth": 0, "liveNow": False,
        "timepicker": {}, "weekStart": "",
        "links": [{"title": "MOKS dashboards", "type": "dashboards", "tags": ["moks"], "asDropdown": True, "includeVars": False, "keepTime": True,
                   "targetBlank": False, "icon": "external link", "tooltip": "", "url": ""}],
        "annotations": {"list": ANNOTATIONS},
        "templating": {"list": [*VARIABLES, *([COMPONENT] if component else [])]},
        "panels": layout(panels),
    }


def rate(metric: str, by: str = "", sel: str = J) -> str:
    return f"sum{f' by ({by})' if by else ''} (rate({metric}{{{sel}}}[$__rate_interval]))"


def per(metric: str, by: str = "", sel: str = J) -> str:
    """Events per bar: for counters that move a few times an hour."""
    return f"sum{f' by ({by})' if by else ''} (increase({metric}{{{sel}}}[$__interval]))"


def total(metric: str, by: str = "", sel: str = J) -> str:
    return f"sum{f' by ({by})' if by else ''} (increase({metric}{{{sel}}}[$__range]))"


def pct(quantile: float, metric: str, by: str = "", sel: str = J, window: str = "$__rate_interval") -> str:
    return f"histogram_quantile({quantile}, sum by (le{', ' + by if by else ''}) (rate({metric}_bucket{{{sel}}}[{window}])))"


def share(metric: str, matcher: str) -> str:
    """The part of a counter's increase over the time range that matches `matcher`, of all of it."""
    return f"{total(metric, sel=J + ', ' + matcher)} / clamp_min({total(metric)}, 1)"


def range_pct(quantile: float, metric: str, by: str = "", sel: str = J) -> str:
    return f"histogram_quantile({quantile}, sum by (le{', ' + by if by else ''}) (increase({metric}_bucket{{{sel}}}[$__range])))"


OK_RED = [("green", None), ("red", 1)]
LOGS_HELP = "Lines are logfmt after Alloy; their trace_id finds the trace in Tempo (main org, signed in). Full lines (with redacted errors) are in kubectl logs."


def overview() -> dict[str, Any]:
    p = [
        row("Now"),
        stat("Processes scraped", f"sum(up{{{J}}}) / count(up{{{J}}})", "Share of MOKS processes Prometheus can scrape (api, worker, crewai, executor, terminal). Below 100% "
             "means a pod is down or its metrics port is unreachable (alert MoksMetricsDown).", unit="percentunit", steps=[("red", None), ("orange", 0.99), ("green", 1)],
             no_value="no target", w=4),
        stat("API requests/s", rate("moks_http_requests_total"), "Requests the API answered per second, every route and status.", unit="reqps", decimals=2, graph=True),
        stat("API 5xx (10m)", f"sum(rate(moks_http_requests_total{{{J}, status=~\"5..\"}}[10m])) / clamp_min(sum(rate(moks_http_requests_total{{{J}}}[10m])), 1e-9)",
             "Share of API responses that were server errors over the last 10 minutes. MoksApiErrors fires above 5%.", unit="percentunit", decimals=2,
             steps=[("green", None), ("orange", 0.01), ("red", 0.05)]),
        stat("API p95", pct(0.95, "moks_http_request_duration_seconds", sel=J, window="10m"), "95th percentile API latency over 10 minutes, every route. "
             "Slow routes are on the Traffic row.", unit="s", steps=[("green", None), ("orange", 1), ("red", 2.5)], no_value="no requests"),
        stat("Investigations running", f"sum(moks_investigations_running{{{J}}})", "Investigations running across workers right now.", graph=True),
        stat("Oldest job waiting", f"max(moks_jobs_oldest_age_seconds{{{J}}})", "Age of the oldest due job or queued investigation in any class. "
             "MoksJobsWaiting fires after 15 minutes.", unit="s", steps=[("green", None), ("orange", 300), ("red", 900)]),
        stat("Investigations", total("moks_investigations_total"), "Investigations that finished in the selected time range, any outcome and engine."),
        stat("Investigations failed", share("moks_investigations_total", 'outcome="failed"'),
             "Share of investigations in the time range that ended failed (not cancelled or abandoned).", unit="percentunit",
             steps=[("green", None), ("orange", 0.1), ("red", 0.3)]),
        stat("Model spend", total("moks_llm_cost_usd_total"), "Estimated LLM cost in the time range, from the provider's token counts and MOKS's price table.",
             unit="currencyUSD", decimals=2),
        stat("MOKS alerts firing", 'count(ALERTS{alertstate="firing", alertname=~"Moks.*"})', "MOKS alert rules firing now (Prometheus rule groups moks, moks-durability, "
             "moks-search). mk4 has no Alertmanager, so this is where they show.", steps=OK_RED),
        stat("Last good backup", f"(time() - max(moks_backup_last_success_timestamp_seconds{{{J}}})) and on() (max(moks_backup_last_success_timestamp_seconds{{{J}}}) > 0)",
             "Time since the last successful off-cluster backup of the MOKS database (moks-backup CronJob). 'never' means none is recorded.",
             unit="s", steps=[("green", None), ("orange", 10800), ("red", 86400)], no_value="never"),
        stat("Database volume used", f"max(moks_database_size_bytes{{{J}}}) / max(moks_database_volume_bytes{{{J}}})",
             "MOKS database size over its volume (database.volumeGiB). PostgreSQL stops writing when the volume is full.", unit="percentunit",
             steps=[("green", None), ("orange", 0.7), ("red", 0.85)]),

        row("API traffic"),
        ts("Requests by status class", [q(f"sum by (code) (label_replace(rate(moks_http_requests_total{{{J}}}[$__rate_interval]), \"code\", \"${{1}}xx\", \"status\", \"(.)..\"))",
                                          "{{code}}")],
           "API responses per second by status class. 4xx is mostly expired sessions (401) and conflicts (409).", unit="reqps", stack=True,
           overrides=[{"matcher": {"id": "byName", "options": "5xx"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]},
                      {"matcher": {"id": "byName", "options": "4xx"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}}]},
                      {"matcher": {"id": "byName", "options": "2xx"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}}]}]),
        ts("API latency", [q(pct(0.5, "moks_http_request_duration_seconds"), "p50"), q(pct(0.95, "moks_http_request_duration_seconds"), "p95"),
                           q(pct(0.99, "moks_http_request_duration_seconds"), "p99")], "API latency across every route.", unit="s"),
        ts("Slowest routes (p95)", [q(f"topk(5, {pct(0.95, 'moks_http_request_duration_seconds', 'route')})", "{{route}}")],
           "The five routes with the highest 95th percentile latency in each window. Tests of connectors and models wait on the far side.", unit="s"),
        ts("Server errors by route", [q(f"sum by (route, status) (rate(moks_http_requests_total{{{J}, status=~\"5..\"}}[$__rate_interval])) > 0", "{{status}} {{route}}")],
           "Routes answering 5xx. Find the request in Loki with the Request ID the portal shows (event=\"http_request\").", unit="reqps", w=12),
        ts("Policy denials", [q(per("moks_policy_denials_total", "kind"), "{{kind}}")], "Requests refused by RBAC, CSRF, namespace scope, secrets policy, "
           "self-approval and plan-hash checks, per bar. A burst of rbac or csrf after a release is worth a look.", bars=True, interval="5m", calcs=["sum"], w=12),

        row("Work and spend"),
        ts("Investigations by engine and outcome", [q(per("moks_investigations_total", "mode, outcome"), "{{mode}} · {{outcome}}")],
           "Finished investigations per bar. Engines: llm and deterministic (LangGraph), crewai, crewai_fallback, unified_agents and unified_rules; "
           "*_fallback means another engine wrote the report.", bars=True, interval="15m", calcs=["sum"]),
        ts("Jobs waiting", [q(f"max by (job_class) (moks_jobs_queued{{{J}}})", "{{job_class}} queued"),
                            q(f"max by (job_class) (moks_jobs_oldest_age_seconds{{{J}}}) / 60", "{{job_class}} oldest (min)")],
           "Queued jobs per class (interactive = investigations) and the oldest due one, in minutes. Details on MOKS · Jobs, connectors and data."),
        ts("Model spend by model", [q(per("moks_llm_cost_usd_total", "model"), "{{model}}")], "Estimated LLM cost per bar, by model.",
           unit="currencyUSD", bars=True, interval="15m", calcs=["sum"]),

        row("Processes"),
        ts("CPU by process", [q(f"sum by (instance) (rate(process_cpu_seconds_total{{{PROCESSES}}}[$__rate_interval]))", "{{instance}}")],
           "CPU seconds per second of each MOKS process. instance is the pod name with the pod-discovery scrape job, its IP with the DNS one.", unit="short", w=12),
        ts("Memory by process", [q(f"max by (instance) (process_resident_memory_bytes{{{PROCESSES}}})", "{{instance}}")],
           "Resident memory of each MOKS process. The crewai worker grows with concurrent crews.", unit="bytes", w=12),

        row("Logs"),
        logs("Warnings and errors", '{namespace="$namespace", ' + APP_CONTAINERS + ', level=~"warn|error"}', LOGS_HELP),
    ]
    return dashboard("moks-overview", "MOKS · Overview", "MOKS at a glance: is it up, is it answering, is work waiting, what does it cost. "
                     "The other MOKS dashboards are under the link at the top.", p, tags=["sre", "overview"], time_from="now-6h", component=True)


def crewai() -> dict[str, Any]:
    flows = "moks_crewai_runs_total"
    p = [
        row("Investigations"),
        stat("Investigations", total("moks_investigations_total"), "Investigations that finished in the time range, every engine and outcome."),
        stat("Completed", share("moks_investigations_total", 'outcome="completed"'),
             "Share of investigations in the time range that completed.", unit="percentunit", steps=[("red", None), ("orange", 0.7), ("green", 0.9)], no_value="-"),
        stat("Crew runs", total(flows), "CrewAI console flows asked to run in the time range (workload, incident and unified investigations)."),
        stat("Crews fell back or unavailable", total(flows, sel=J + ', outcome=~"fallback|unavailable"'), "Crew runs that LangGraph answered instead, "
             "or that could not start (CrewAI off, flow switched off, no slot, outside scope).", steps=[("green", None), ("orange", 1)]),
        stat("Crews running", f"sum(moks_crewai_running{{{J}}})", "Crews holding a slot now, across workers (crewai_max_concurrent per worker).", graph=True),
        stat("Investigation p95", range_pct(0.95, "moks_investigation_duration_seconds"), "95th percentile investigation wall time in the time range, every engine.",
             unit="s", no_value="-"),
        ts("Investigations by engine and outcome", [q(per("moks_investigations_total", "mode, outcome"), "{{mode}} · {{outcome}}")],
           "Finished investigations per bar. crewai_fallback: the crew's answer did not survive validation and LangGraph's report writer ran over its evidence.",
           bars=True, interval="15m", calcs=["sum"]),
        ts("Investigation duration", [q(pct(0.5, "moks_investigation_duration_seconds", "mode", window="$__interval"), "p50 {{mode}}"),
                                      q(pct(0.95, "moks_investigation_duration_seconds", "mode", window="$__interval"), "p95 {{mode}}")],
           "Wall time by engine. Points only where investigations finished in that window.", unit="s", interval="15m", points=True),
        ts("Event streams", [q(f"sum(moks_sse_streams_open{{{J}}})", "open"), q(per("moks_sse_streams_total", "outcome"), "closed · {{outcome}}")],
           "Browser event streams that follow investigations: open now, and how closed ones ended per bar.", interval="5m"),

        row("CrewAI flows"),
        ts("Crew runs by flow and outcome", [q(per(flows, "flow, outcome"), "{{flow}} · {{outcome}}")],
           "Per bar. completed and partial are the crew's own status; fallback means LangGraph wrote the report; unavailable means the flow never started.",
           bars=True, interval="15m", calcs=["sum"]),
        ts("Crew errors and early stops", [q(per("moks_crewai_errors_total", "code"), "{{code}}")],
           "Per bar, by code: budget_exhausted, task_timeout, iteration_limit, invalid_output, provider_error, run_timeout, fallback. A run can have several.",
           bars=True, interval="15m", calcs=["sum"]),
        ts("Crew slots in use", [q(f"sum(moks_crewai_running{{{J}}})", "crews running"), q(f"sum(moks_investigations_running{{{J}}})", "investigations running")],
           "Crews holding a slot, next to all running investigations. A crew that waits too long for a slot falls back or reports unavailable."),

        row("Incident flow: seven agents"),
        bar("Step p95 duration", [q(range_pct(0.95, "moks_crewai_task_duration_seconds", "task"), "{{task}}")],
            "95th percentile wall time of each step in the time range (plan, kubernetes, observability, change, knowledge, hypotheses, review, rca).", unit="s"),
        bar("Steps by outcome", [q(f"{total('moks_crewai_tasks_total', 'task, status')} > 0", "{{task}} · {{status}}")],
            "Step attempts in the time range. partial: a limit ended the step with what it had; invalid_output: the answer never matched the step's contract."),
        bar("Tokens by agent", [q(f"{total('moks_crewai_tokens_total', 'agent')} > 0", "{{agent}}")],
            "Model tokens (input and output) each agent used in the time range. The token budget is per investigation (CrewAI console limits)."),
        ts("Tokens by agent over time", [q(per("moks_crewai_tokens_total", "agent"), "{{agent}}")], "Tokens per bar, by agent.", bars=True, interval="15m",
           calcs=["sum"], w=12),
        ts("Step outcomes over time", [q(per("moks_crewai_tasks_total", "status"), "{{status}}")], "Step attempts per bar by outcome.", bars=True,
           interval="15m", calcs=["sum"], w=12,
           overrides=[{"matcher": {"id": "byName", "options": "failed"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]},
                      {"matcher": {"id": "byName", "options": "done"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}}]}]),

        row("Evidence and validation"),
        bar("Conclusions as MOKS graded them", [q(f"{total('moks_crewai_conclusions_total', 'kind, status')} > 0", "{{kind}} · {{status}}")],
            "Findings and hypotheses of incident runs in the time range, by the status MOKS's grading gave them (never the model's own word)."),
        ts("Validation changes", [q(per("moks_crewai_validation_total", "decision"), "{{decision}}")],
           "Per bar: citations rejected (ids no evidence has), claims removed, and hypotheses whose confidence MOKS lowered.", bars=True, interval="15m", calcs=["sum"]),
        ts("Reviewer evidence requests", [q(per("moks_crewai_evidence_requests_total", "status"), "{{status}}")],
           "Evidence the reviewer asked for, per bar: collected, already_collected, refused (no specialist has the tool, or policy said no) or not_run.",
           bars=True, interval="15m", calcs=["sum"]),

        row("Unified pipeline"),
        bar("Stage outcomes", [q(total("moks_unified_stages_total", "stage, status", sel=J + ', status!="completed"') + " > 0", "{{stage}} · {{status}}")],
            "Unified-pipeline stages that did not complete in the time range: partial (time or tool budget), failed, skipped or cancelled.",
            steps=[("orange", None)]),
        bar("Stage p95 duration", [q(range_pct(0.95, "moks_unified_stage_duration_seconds", "stage"), "{{stage}}")],
            "95th percentile wall time of each of the fifteen stages in the time range. Skipped stages are not timed.", unit="s"),
        ts("Stages over time", [q(per("moks_unified_stages_total", "status"), "{{status}}")], "Stage ends per bar, by status.", bars=True, interval="15m",
           calcs=["sum"]),

        row("Tools"),
        ts("Tool calls by outcome", [q(rate("moks_tool_calls_total", "outcome"), "{{outcome}}")],
           "Read-only tool calls from every engine. forbidden and denied are policy; timeout and unavailable are the dependency.", unit="cps", stack=True),
        bar("Most used tools", [q(f"topk(15, {total('moks_tool_calls_total', 'tool')})", "{{tool}}")], "Tool calls in the time range."),
        bar("Tools that fail", [q(total("moks_tool_calls_total", "tool", sel=J + ', outcome!~"ok|invalid_args"') + " / " + total("moks_tool_calls_total", "tool") + " > 0",
                                  "{{tool}}")],
            "Share of each tool's calls in the time range that did not succeed (invalid arguments excluded). MoksToolCallsFailing fires above 50%.",
            unit="percentunit", steps=[("green", None), ("orange", 0.2), ("red", 0.5)]),

        row("Logs"),
        logs("Investigation and crew events", '{namespace="$namespace", container=~"worker|crewai"} | logfmt | '
             'event=~"crewai.*|investigation.*|llm_answer_unused|unified.*|evidence_request"', LOGS_HELP),
    ]
    return dashboard("moks-crewai", "MOKS · Investigations and CrewAI", "Investigations by engine, the CrewAI flows (incident flow steps, agents, "
                     "graded conclusions, validation) and the unified pipeline's stages. Labels are names and statuses, never targets or model text.",
                     p, tags=["crewai", "investigations"])


def llm() -> dict[str, Any]:
    p = [
        row("Now"),
        stat("Spend", total("moks_llm_cost_usd_total"), "Estimated LLM cost in the time range.", unit="currencyUSD", decimals=2),
        stat("Model calls", total("moks_llm_requests_total"), "Calls to chat models in the time range, every outcome."),
        stat("Failed calls", share("moks_llm_requests_total", 'outcome!="ok"'),
             "Share of model calls in the time range that failed (auth, rate_limited, unavailable, bad_response, timeout...).", unit="percentunit",
             steps=[("green", None), ("orange", 0.05), ("red", 0.2)]),
        stat("Input tokens", total("moks_llm_tokens_total", sel=J + ', direction="input"'), "Input tokens in the time range."),
        stat("Output tokens", total("moks_llm_tokens_total", sel=J + ', direction="output"'), "Output tokens in the time range."),
        stat("Answers not used", total("moks_llm_answers_unused_total"), "Model answers MOKS could not use (not JSON, schema invalid, budget) so a rule-based "
             "one was written.", steps=[("green", None), ("orange", 1)]),

        row("Calls"),
        ts("Calls by model and outcome", [q(per("moks_llm_requests_total", "provider, model, outcome"), "{{provider}}/{{model}} · {{outcome}}")],
           "Model calls per bar. Embedding models served by a chat provider (Ollama) show here too.", bars=True, interval="5m", calcs=["sum"]),
        ts("Latency by provider", [q(pct(0.5, "moks_llm_latency_seconds", "provider", window="$__interval"), "p50 {{provider}}"),
                                   q(pct(0.95, "moks_llm_latency_seconds", "provider", window="$__interval"), "p95 {{provider}}")],
           "Model call latency.", unit="s", interval="5m", points=True),
        ts("Retries and server-side fallbacks", [q(per("moks_llm_retries_total", "provider"), "retried · {{provider}}"),
                                                 q(per("moks_llm_fallbacks_total", "model"), "served by another model · {{model}}")],
           "Calls repeated after a rate limit or overload, and answers a provider served from a different model than the one asked.", bars=True, interval="5m",
           calcs=["sum"]),
        ts("Answers not used, by stage and reason", [q(per("moks_llm_answers_unused_total", "stage, reason"), "{{stage}} · {{reason}}")],
           "BudgetExceeded means the investigation's token budget had no room left for the report (max_tokens_per_investigation).", bars=True, interval="15m",
           calcs=["sum"], w=12),
        ts("Tokens per minute", [q(f"sum by (model, direction) (rate(moks_llm_tokens_total{{{J}}}[$__rate_interval])) * 60", "{{model}} {{direction}}")],
           "Token throughput by model and direction.", w=12),

        row("Spend"),
        ts("Spend by model", [q(per("moks_llm_cost_usd_total", "model"), "{{model}}")], "Estimated cost per bar.", unit="currencyUSD", bars=True,
           interval="1h", calcs=["sum"], w=12),
        bar("Spend by model in the range", [q(f"{total('moks_llm_cost_usd_total', 'provider, model')} > 0", "{{provider}}/{{model}}")],
            "Estimated cost per model over the time range.", unit="currencyUSD", decimals=2, w=12),

        row("Embeddings"),
        ts("Embedding calls", [q(per("moks_embedding_requests_total", "provider, outcome"), "{{provider}} · {{outcome}}")],
           "Calls to the embedding provider per bar (knowledge sync and question embeddings).", bars=True, interval="5m", calcs=["sum"]),
        ts("Embedding latency", [q(pct(0.5, "moks_embedding_latency_seconds", "provider", window="$__interval"), "p50 {{provider}}"),
                                 q(pct(0.95, "moks_embedding_latency_seconds", "provider", window="$__interval"), "p95 {{provider}}")],
           "Embedding call latency.", unit="s", interval="5m", points=True),
        ts("Embedding cache", [q(per("moks_rag_embedding_inputs_total", "outcome"), "{{outcome}}")],
           "Texts that needed a vector: computed by the model, or reused from the embedding cache.", bars=True, interval="15m", calcs=["sum"]),
    ]
    return dashboard("moks-llm", "MOKS · Models and spend", "Chat and embedding model calls: outcomes, latency, tokens and estimated cost.",
                     p, tags=["llm", "cost"])


def knowledge() -> dict[str, Any]:
    rr = 'job="moks", component="reranker"'
    p = [
        row("Now"),
        stat("Searches", total("moks_retrieval_searches_total"), "Knowledge searches in the time range, by any mode."),
        stat("Answered with less", total("moks_retrieval_fallbacks_total") + " / clamp_min(" + total("moks_retrieval_searches_total") + ", 1)",
             "Searches answered with less than was asked for (no vectors, no reranker, keyword only), over all searches. MoksRetrievalFallingBack fires on any.",
             unit="percentunit", steps=[("green", None), ("orange", 0.01), ("red", 0.2)]),
        stat("Search p95", range_pct(0.95, "moks_retrieval_latency_seconds"), "95th percentile retrieval latency in the time range.", unit="s", no_value="-"),
        stat("Reranked", share("moks_rerank_calls_total", 'outcome="applied"'),
             "Share of rerank attempts in the time range whose order was applied (others timed out, failed or were skipped).", unit="percentunit",
             steps=[("red", None), ("orange", 0.8), ("green", 0.95)], no_value="-"),
        stat("Embedding cache reuse", share("moks_rag_embedding_inputs_total", 'outcome="reused"'),
             "Share of texts needing a vector that the embedding cache answered.", unit="percentunit", no_value="-"),
        stat("Documents indexed", f"sum(max by (source) (moks_indexing_documents{{{J}, state=\"active\"}}))", "Active documents across knowledge sources, "
             "as of each source's last sync."),

        row("Search"),
        ts("Searches by mode", [q(per("moks_retrieval_searches_total", "mode"), "{{mode}}")], "The mode that actually ran: hybrid, vector or keyword.",
           bars=True, interval="15m", calcs=["sum"]),
        ts("Retrieval latency", [q(pct(0.5, "moks_retrieval_latency_seconds", "backend", window="$__interval"), "p50 {{backend}}"),
                                 q(pct(0.95, "moks_retrieval_latency_seconds", "backend", window="$__interval"), "p95 {{backend}}")],
           "Search latency, embeddings and reranking included.", unit="s", interval="15m", points=True),
        ts("Answered with less, by reason", [q(per("moks_retrieval_fallbacks_total", "reason"), "{{reason}}")],
           "Why a search gave less than asked: the reason says which part was missing.", bars=True, interval="15m", calcs=["sum"]),
        ts("Knowledge stages of investigations", [q(per("moks_knowledge_retrievals_total", "stage, outcome"), "{{stage}} · {{outcome}}")],
           "The initial and refresh retrieval inside investigations: retrieved, empty, skipped or failed.", bars=True, interval="15m", calcs=["sum"], w=12),
        ts("Documents by source", [q(f"max by (source, state) (moks_indexing_documents{{{J}}})", "{{source}} · {{state}}")],
           "Active and excluded documents per knowledge source, set at each sync.", w=12),

        row("Reranking (as MOKS sees it)"),
        ts("Rerank attempts", [q(per("moks_rerank_calls_total", "outcome"), "{{outcome}}")], "Reranking attempts by outcome, per bar.", bars=True, interval="15m",
           calcs=["sum"]),
        ts("Rerank round trip", [q(pct(0.5, "moks_rerank_seconds", window="$__interval"), "p50"), q(pct(0.95, "moks_rerank_seconds", window="$__interval"), "p95")],
           "Round trip to the reranking service.", unit="s", interval="15m", points=True),
        ts("Candidates and truncation", [q(pct(0.5, "moks_rerank_candidates", window="$__interval"), "candidates p50"),
                                         q(pct(0.95, "moks_rerank_candidates", window="$__interval"), "candidates p95"),
                                         q(per("moks_rerank_truncated_passages_total"), "passages only partly scored")],
           "Authorized candidates sent per rerank, and passages longer than the model reads.", interval="15m", points=True),

        row("Reranker and embedder service"),
        text("Read from the `moks-reranker` (and `moks-embedder`) pods themselves. They are outside the `moks-metrics` Service, so these panels stay empty "
             "until Prometheus discovers MOKS by pod (the pod-discovery `moks` job in the kubernetes repository).", h=2),
        ts("Reranker requests", [q(rate("reranker_requests_total", "outcome", rr), "{{outcome}}")], "Requests the reranker served, by outcome.", unit="cps",
           stack=True),
        ts("Reranker queue and inference", [q(pct(0.95, "reranker_queue_wait_seconds", sel=rr), "queue wait p95"),
                                            q(pct(0.95, "reranker_inference_seconds", sel=rr), "inference p95")],
           "Time waiting for a model slot, and model time, per request.", unit="s"),
        ts("Reranker slots and failures", [q(f"sum(reranker_slots_held{{{rr}}})", "slots held"),
                                           q(per("reranker_model_load_failures_total", sel=rr), "model load failures"),
                                           q(per("reranker_truncated_passages_total", sel=rr), "passages truncated")],
           "Slots held by running or queued work, and model load failures.", interval="5m"),
        ts("Embedder requests", [q(rate("embedder_requests_total", "outcome", 'job="moks", component=~"reranker|embedder"'), "{{outcome}}")],
           "Requests to the moks-embedder service (bge-base), by outcome. Empty while the embedder is not deployed.", unit="cps", stack=True, w=12),
        ts("Embedder inference", [q(pct(0.95, "embedder_inference_seconds", sel='job="moks", component=~"reranker|embedder"'), "inference p95"),
                                  q(pct(0.95, "embedder_inputs", sel='job="moks", component=~"reranker|embedder"'), "inputs per request p95")],
           "Model time and batch size per request.", w=12),
    ]
    return dashboard("moks-knowledge", "MOKS · Knowledge and retrieval", "Knowledge search in PostgreSQL + pgvector: modes, latency, what was missing, "
                     "embeddings and reranking.", p, tags=["knowledge", "rag"])


def platform() -> dict[str, Any]:
    p = [
        row("Now"),
        stat("Jobs queued", f"sum(max by (job_class) (moks_jobs_queued{{{J}}}))", "Jobs waiting to run, every class (interactive = investigations).", graph=True),
        stat("Jobs running", f"sum(max by (job_class) (moks_jobs_running{{{J}}}))", "Jobs running across workers.", graph=True),
        stat("Jobs refused (queue full)", total("moks_jobs_rejected_total"), "Jobs refused in the time range because their queue was full.", steps=OK_RED),
        stat("Jobs abandoned", total("moks_jobs_abandoned_total"), "Jobs whose worker stopped in the time range (they are retried or failed by the reaper).",
             steps=[("green", None), ("orange", 1)]),
        stat("Database size", f"max(moks_database_size_bytes{{{J}}})", "Size of the MOKS database.", unit="bytes", graph=True),
        stat("Last backup run", f"max(moks_backup_last_run_success{{{J}}})",
             "The latest moks-backup run, as the worker last read it. 'no run recorded' until a backup has run (mk4 has no moks-backup CronJob).",
             steps=[("red", None), ("green", 1)], no_value="no run recorded",
             mappings=[{"type": "value", "options": {"0": {"text": "failed", "color": "red", "index": 0}, "1": {"text": "succeeded", "color": "green", "index": 1}}},
                       {"type": "special", "options": {"match": "nan", "result": {"text": "no run recorded", "color": "orange", "index": 2}}}]),

        row("Job queue"),
        ts("Queued by class", [q(f"max by (job_class) (moks_jobs_queued{{{J}}})", "{{job_class}}")], "Jobs waiting to run, by class."),
        ts("Running by class", [q(f"max by (job_class) (moks_jobs_running{{{J}}})", "{{job_class}}")], "Jobs running across workers, by class."),
        ts("Oldest due job", [q(f"max by (job_class) (moks_jobs_oldest_age_seconds{{{J}}})", "{{job_class}}")],
           "Age of the oldest due job per class. MoksJobsWaiting fires above 15 minutes.", unit="s",
           overrides=[]),
        ts("Schedule delay p95", [q(pct(0.95, "moks_job_schedule_delay_seconds", "job_class", window="$__interval"), "{{job_class}}")],
           "How long a due job waited for a worker.", unit="s", interval="15m", points=True),
        bar("Runtime p95 by job type", [q(range_pct(0.95, "moks_job_runtime_seconds", "type"), "{{type}}")],
            "95th percentile job duration in the time range, by type (knowledge_sync, eval_run, ml_anomaly_train, learning_purge...).", unit="s"),
        ts("Retries, abandoned and refused", [q(per("moks_job_retries_total", "type"), "retried · {{type}}"),
                                              q(per("moks_jobs_abandoned_total", "type"), "abandoned · {{type}}"),
                                              q(per("moks_jobs_rejected_total", "job_class"), "refused · {{job_class}}")],
           "Per bar.", bars=True, interval="15m", calcs=["sum"]),

        row("Connectors"),
        ts("Connector calls by outcome", [q(rate("moks_connector_requests_total", "connector_type, outcome"), "{{connector_type}} · {{outcome}}")],
           "Calls to Kubernetes, Prometheus, Loki, Grafana, Git, Argo CD, PostgreSQL, Redis and the rest.", unit="cps", stack=True),
        ts("Connector latency p95", [q(pct(0.95, "moks_connector_latency_seconds", "connector_type"), "{{connector_type}}")], "Per connector type.", unit="s"),
        bar("Connector failures in the range", [q(total("moks_connector_requests_total", "connector_type, operation, outcome", sel=J + ', outcome!="ok"') + " > 0",
                                                  "{{connector_type}} {{operation}} · {{outcome}}")],
            "Calls that did not succeed in the time range. auth: the stored credential is wrong or expired; unavailable: the service did not answer.",
            steps=[("orange", None)]),

        row("Approvals and actions"),
        ts("Approval decisions", [q(per("moks_approvals_total", "decision"), "{{decision}}")], "Approve, reject and force_approve, per bar.", bars=True,
           interval="1h", calcs=["sum"]),
        ts("Remediation actions", [q(per("moks_actions_total", "action, outcome"), "{{action}} · {{outcome}}")], "Approved actions by outcome, per bar.",
           bars=True, interval="1h", calcs=["sum"]),
        ts("Policy denials", [q(per("moks_policy_denials_total", "kind"), "{{kind}}")], "Requests refused by policy, by kind, per bar.", bars=True,
           interval="15m", calcs=["sum"]),

        row("Database and backups"),
        ts("Database size and volume", [q(f"max(moks_database_size_bytes{{{J}}})", "database"), q(f"max(moks_database_volume_bytes{{{J}}})", "volume")],
           "The MOKS database against its volume. MoksDatabaseVolumeFilling fires above 70%.", unit="bytes", w=12),
        ts("Time since the last good backup", [q(f"(time() - max(moks_backup_last_success_timestamp_seconds{{{J}}})) and on() "
                                                 f"(max(moks_backup_last_success_timestamp_seconds{{{J}}}) > 0)", "age")],
           "Empty when no backup has ever succeeded. MoksBackupOverdue fires above 3 hours.", unit="s", w=12),

        row("Processes and telemetry"),
        ts("CPU by process", [q(f"sum by (instance) (rate(process_cpu_seconds_total{{{PROCESSES}}}[$__rate_interval]))", "{{instance}}")],
           "CPU seconds per second.", w=8),
        ts("Memory by process", [q(f"max by (instance) (process_resident_memory_bytes{{{PROCESSES}}})", "{{instance}}")], "Resident memory.", unit="bytes", w=8),
        ts("Trace pipeline", [q('sum(rate(otelcol_receiver_accepted_spans{job="otel-collector"}[$__rate_interval]))', "spans in"),
                              q('sum(rate(otelcol_exporter_sent_spans{job="otel-collector"}[$__rate_interval]))', "spans out"),
                              q('sum(rate(otelcol_receiver_refused_spans{job="otel-collector"}[$__rate_interval]))', "refused")],
           "The mk4 OpenTelemetry collector that carries MOKS's (and CrewAI's) spans to Tempo. Refused spans are lost.", unit="cps", w=8),
    ]
    return dashboard("moks-platform", "MOKS · Jobs, connectors and data", "The machinery: job queue, connectors, approvals and actions, database and "
                     "backups, processes.", p, tags=["jobs", "durability"], component=True)


def ml() -> dict[str, Any]:
    p = [
        text("**Shadow mode.** MOKS's models score and correlate, people judge, nothing is acted on. Empty panels mean anomaly detection is disabled "
             "(Settings, Anomaly detection) or no champion is active. After a deploy with a new feature schema, champions report `stale_schema` until "
             "retrained (docs/ML.md).", h=3),
        row("Scoring"),
        ts("Observations by status", [q(per("moks_ml_observations_total", "capability, status"), "{{capability}} · {{status}}")],
           "Observations recorded per bar.", bars=True, interval="1h", calcs=["sum"]),
        ts("Flagged windows", [q(per("moks_ml_flags_total", "algorithm, level, role"), "{{algorithm}} {{role}} · {{level}}")],
           "Scored windows flagged unusual, by algorithm, level and model role (champion or challenger).", bars=True, interval="1h", calcs=["sum"]),
        ts("Inference p95 per window", [q(pct(0.95, "moks_ml_inference_seconds", "capability, algorithm", window="$__interval"), "{{capability}} {{algorithm}}")],
           "Time to score one cohort window.", unit="s", interval="1h", points=True),
        ts("Fallbacks to deterministic", [q(per("moks_ml_fallbacks_total", "capability, reason"), "{{capability}} · {{reason}}")],
           "ML paths that fell back to rules, per bar.", bars=True, interval="1h", calcs=["sum"], w=12),
        bar("Missing features", [q(f"topk(10, {total('moks_ml_missing_features_total', 'feature')}) > 0", "{{feature}}")],
            "Scored windows in the time range where a feature was absent or under half coverage. mk4 lacks kube-state-metrics, so restarts and "
            "node pressure are often missing.", w=12),

        row("Model health"),
        ts("Input drift (PSI), top 10", [q(f"topk(10, max by (cohort, feature) (moks_ml_feature_drift_psi{{{J}}}))", "{{cohort}} {{feature}}")],
           "Population stability index of each feature against the champion's training data, for the last scored window. Above 0.25 is a large shift.",
           overrides=[]),
        stat("Champions to review", f"count(max by (algorithm, reason) (moks_ml_model_degraded{{{J}}}) == 1)",
             "Champions whose health check recommends review (drift, flag rate or judged precision).", steps=OK_RED, w=4, h=8),
        bar("Why review", [q(f"max by (algorithm, reason) (moks_ml_model_degraded{{{J}}}) == 1", "{{algorithm}} · {{reason}}")],
            "The champions flagged for review, and why.", w=12, steps=[("orange", None)]),

        row("Correlation"),
        ts("Correlation groups", [q(per("moks_ml_correlation_groups_total", "event"), "{{event}}")], "Groups created, updated and closed, per bar.", bars=True,
           interval="1h", calcs=["sum"]),
        ts("Grouping time p95", [q(pct(0.95, "moks_ml_correlation_seconds", window="$__interval"), "p95")],
           "Time to group one environment's anomalies, context included.", unit="s", interval="1h", points=True),
        ts("Context that could not be read", [q(per("moks_ml_context_gaps_total", "source"), "{{source}}")],
           "Correlation context (mesh calls, nodes, rollouts) that a source could not give, per bar.", bars=True, interval="1h", calcs=["sum"]),
    ]
    return dashboard("moks-ml", "MOKS · ML anomaly detection", "MOKS's own models in shadow mode: scoring, flags, drift, champion health and incident "
                     "correlation.", p, tags=["ml"], time_from="now-7d")


DASHBOARDS = {"moks-overview.json": overview, "moks-crewai.json": crewai, "moks-llm.json": llm, "moks-knowledge.json": knowledge,
              "moks-platform.json": platform, "moks-ml.json": ml}


def render() -> dict[str, str]:
    return {name: dump(standardize(build(), "public", "MOKS")) for name, build in DASHBOARDS.items()}


def main(argv: list[str]) -> int:
    files = render()
    if "--check" in argv:
        stale = [n for n, body in files.items() if not (OUT / n).exists() or (OUT / n).read_text() != body]
        extra = sorted(p.name for p in OUT.glob("moks-*.json") if p.name not in files)
        for n in stale:
            print(f"{OUT / n} differs from build.py; run python3 generators/moks/build.py")
        for n in extra:
            print(f"{OUT / n} is not built by build.py")
        return 1 if stale or extra else 0
    OUT.mkdir(exist_ok=True)
    for n, body in files.items():
        (OUT / n).write_text(body)
    print(f"wrote {len(files)} dashboards to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
