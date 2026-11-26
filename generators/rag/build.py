#!/usr/bin/env python3
"""Builds the RAG platform's Grafana dashboards in dashboards/public/RAG/ (org Public, folder RAG).

    python3 generators/rag/build.py           # write the JSON files
    python3 generators/rag/build.py --check   # exit 1 when a file differs from what this script builds (CI)

The platform is mrjithendar/moks-rag, namespace rag on mk4: rag-api (jithendar.com/rag) and Open WebUI
(jithendar.com/chat) in front of Qdrant, bge-m3 (Ollama), docling-serve and LiteLLM. None of them exports metrics that
Prometheus scrapes, so the dashboards read what the cluster already collects:

    traffic      the Istio sidecars (job envoy-stats): istio_requests_total, its latency histogram, istio_tcp_* for
                 connections that leave the mesh
    resources    cAdvisor (job kubernetes-cadvisor) and the kubelet's volume stats (job kubernetes-kubelet)
    operations   rag-api's log events in Loki. Alloy keeps only allowlisted fields (k8s-deployments:
                 observability/loki/config.alloy): the `event` name survives, rag-api's path, status_code, duration_ms,
                 service and collection do not.

Edit the dashboards here, not in the JSON: tests/test_rag.py and the --check in CI fail when they disagree. Whether
each query has data on mk4: python3 scripts/audit_live.py rag. Standard library only.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from dashboards import dump, standardize  # noqa: E402

OUT = ROOT / "dashboards" / "public" / "RAG"
PROM = {"type": "prometheus", "uid": "roboshop-prometheus"}
LOKI = {"type": "loki", "uid": "roboshop-loki"}
REPO = "https://github.com/mrjithendar/moks-rag"

# Component (its Deployment or StatefulSet, and its Service) → its main container. moks-rag: deploy/k8s/*.yaml and
# deploy/argocd/qdrant-values.yaml. A new component goes here; every panel follows.
COMPONENTS = {"rag-api": "rag-api", "open-webui": "open-webui", "qdrant": "qdrant", "bge-m3": "ollama",
              "docling-serve": "docling-serve", "litellm": "litellm"}

CADVISOR = 'job="kubernetes-cadvisor", namespace="rag"'
KUBELET = 'job="kubernetes-kubelet", namespace="rag"'
APP = f'{CADVISOR}, container=~"{"|".join(COMPONENTS.values())}"'  # the main containers, not the sidecars
# What each service's own sidecar answered: it survives the callers' restarts.
MESH_IN = 'job="envoy-stats", reporter="destination", destination_service_namespace="rag"'
# Requests a caller's sidecar could not get to a pod (no healthy pod, connection failed, overflow, no route): only the
# caller sees them.
UNDELIVERED = 'job="envoy-stats", reporter="source", destination_service_namespace="rag", response_flags=~".*(UH|UF|UO|NR).*"'
# Connections that leave the mesh (Istio's PassthroughCluster): TLS to the internet, so bytes but no requests.
INTERNET = 'job="envoy-stats", reporter="source", source_workload_namespace="rag", destination_service_name="PassthroughCluster"'
NOT_UPGRADES = 'response_code!="101"'  # a WebSocket's "request" lasts as long as the connection
FIVE_XX = 'response_code=~"5.."'
RAG_API = 'destination_service_name="rag-api"'
OPEN_WEBUI = 'destination_service_name="open-webui"'
# Deployment pods end in -<template hash>-<5 characters>, StatefulSet pods in -<ordinal>.
POD_COMPONENT = "(.+?)(?:-[a-z0-9]{8,10}-[a-z0-9]{5}|-[0-9]+)"
RAG_API_LOGS = '{namespace="rag", container="rag-api"}'
OPERATIONS = {"rag_ingest": "Ingests", "rag_query": "Queries", "openai_chat_completions": "Chat answers",
              "rag_delete_document": "Deletes"}


# ---- queries ------------------------------------------------------------------------------------------------------

def sel(*parts: str) -> str:
    return ", ".join(p for p in parts if p)


def by_component(expr: str) -> str:
    """cAdvisor names containers, and bge-m3's is ollama: call each series by its component."""
    for component, container in COMPONENTS.items():
        if component != container:
            expr = f'label_replace({expr}, "container", "{component}", "container", "{container}")'
    return expr


def pod_component(expr: str) -> str:
    """For series that have a pod but no container (network): the component, from the pod's name."""
    return f'label_replace({expr}, "component", "$1", "pod", "{POD_COMPONENT}")'


def rate(metric: str, selector: str, by: str = "") -> str:
    return f"sum{f' by ({by})' if by else ''} (rate({metric}{{{selector}}}[$__rate_interval]))"


def quantile(q: float, selector: str, by: str = "") -> str:
    return (f"histogram_quantile({q}, sum by (le{', ' + by if by else ''}) "
            f"(rate(istio_request_duration_milliseconds_bucket{{{sel(selector, NOT_UPGRADES)}}}[$__rate_interval])))")


def counted(series: str, window: str = "$__range") -> str:
    """increase() over the window, plus the start value of a series that first appears inside it, which increase()
    alone reads as 0: a caller's first requests, and those of a pod that started in the window. Traffic here is sparse,
    so that is most of it."""
    return (f"(increase({series}[{window}]) + (min_over_time({series}[{window}]) unless {series} offset {window}) "
            f"or increase({series}[{window}]))")


def requests_in(service: str, codes: str = "") -> str:
    """Requests to `service` in the time range: what its sidecar answered (with `codes`, only those), plus every
    request its callers could not deliver. No series when there were none."""
    answered = f"istio_requests_total{{{sel(MESH_IN, service, codes)}}}"
    lost = f"istio_requests_total{{{sel(UNDELIVERED, service)}}}"
    return f"sum({counted(answered)} or {counted(lost)})"


def server_errors(service: str) -> str:
    """Share of requests in the range that got a 5xx or never arrived; no requests reads as no value, not 0 %."""
    return f"({requests_in(service, FIVE_XX)} or vector(0)) / {requests_in(service)}"


def range_p95(service: str) -> str:
    bucket = f"istio_request_duration_milliseconds_bucket{{{sel(MESH_IN, service, NOT_UPGRADES)}}}"
    return f"histogram_quantile(0.95, sum by (le) ({counted(bucket)}))"


def events(event: str) -> str:
    return f'sum(count_over_time({RAG_API_LOGS} | logfmt | event="{event}" [$__range])) or vector(0)'


# ---- panels -------------------------------------------------------------------------------------------------------

def q(expr: str, legend: str = "", *, ds: dict[str, str] = PROM) -> dict[str, Any]:
    return {"datasource": ds, "expr": expr, "legendFormat": legend or "__auto", "refId": ""}


def _thresholds(steps: list[tuple[str, float | None]] | None) -> dict[str, Any]:
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in steps or [("green", None)]]}


def stat(title: str, expr: str, desc: str, *, unit: str = "short", steps: list[tuple[str, float | None]] | None = None,
         decimals: int | None = None, no_value: str = "0", w: int = 4, h: int = 4, ds: dict[str, str] = PROM,
         mappings: list[dict[str, Any]] | None = None, legend: str = "", named: bool = False) -> dict[str, Any]:
    defaults: dict[str, Any] = {"unit": unit, "thresholds": _thresholds(steps), "color": {"mode": "thresholds"}, "noValue": no_value}
    if decimals is not None:
        defaults["decimals"] = decimals
    if mappings:
        defaults["mappings"] = mappings
    target = {**q(expr, legend, ds=ds), "instant": True, "range": False}
    target.update({"queryType": "instant"} if ds is LOKI else {"format": "time_series"})
    return {"type": "stat", "title": title, "description": desc, "datasource": ds, "_w": w, "_h": h, "targets": [target],
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "background" if named else "value", "graphMode": "none", "justifyMode": "center",
                        "textMode": "value_and_name" if named else "value", "orientation": "auto", "wideLayout": True}}


def ts(title: str, targets: list[dict[str, Any]], desc: str, *, unit: str = "short", w: int = 12, h: int = 8,
       bars: bool = False, interval: str | None = None, calcs: list[str] | None = None, ds: dict[str, str] = PROM,
       max_: float | None = None) -> dict[str, Any]:
    custom = {"drawStyle": "bars" if bars else "line", "lineWidth": 1, "fillOpacity": 70 if bars else 10,
              "gradientMode": "none", "showPoints": "never", "pointSize": 4, "spanNulls": False,
              "stacking": {"mode": "normal" if bars else "none", "group": "A"}, "axisSoftMin": 0, "barAlignment": 0}
    defaults: dict[str, Any] = {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"}}
    if max_ is not None:
        defaults["max"] = max_
    if ds is LOKI:
        targets = [{**t, "queryType": "range"} for t in targets]
    p = {"type": "timeseries", "title": title, "description": desc, "datasource": ds, "_w": w, "_h": h, "targets": targets,
         "fieldConfig": {"defaults": defaults, "overrides": []},
         "options": {"legend": {"displayMode": "table" if calcs else "list", "placement": "bottom", "showLegend": True,
                                "calcs": calcs or []},
                     "tooltip": {"mode": "multi", "sort": "desc"}}}
    if interval:
        p["interval"] = interval
    return p


def bars(title: str, targets: list[dict[str, Any]], desc: str, *, unit: str = "percentunit", w: int = 8, h: int = 8,
         steps: list[tuple[str, float | None]] | None = None, no_value: str = "No data") -> dict[str, Any]:
    """Horizontal bars, one per series, at the end of the time range."""
    defaults: dict[str, Any] = {"unit": unit, "min": 0, "thresholds": _thresholds(steps), "color": {"mode": "thresholds"},
                                "noValue": no_value}
    if unit == "percentunit":
        defaults["max"] = 1
    return {"type": "bargauge", "title": title, "description": desc, "datasource": PROM, "_w": w, "_h": h,
            "targets": [{**t, "instant": True, "range": False, "format": "time_series"} for t in targets],
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True, "valueMode": "color",
                        "namePlacement": "auto", "sizing": "auto", "minVizHeight": 10, "maxVizHeight": 300,
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}}


def text(content: str, *, h: int = 3) -> dict[str, Any]:
    return {"type": "text", "title": "", "_w": 24, "_h": h, "options": {"mode": "markdown", "content": content}}


def row(title: str) -> dict[str, Any]:
    return {"type": "row", "title": title, "collapsed": False, "panels": [], "_w": 24, "_h": 1}


def layout(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Left to right, wrapping at 24 columns; a row starts on a new line. Ids and refIds follow the order."""
    out, x, y, line_h = [], 0, 0, 0
    for n, p in enumerate(items, start=1):
        w, h = p.pop("_w"), p.pop("_h")
        if p["type"] == "row" or x + w > 24:
            y, x, line_h = y + line_h, 0, 0
        p["id"] = n
        p["gridPos"] = {"h": h, "w": w, "x": x, "y": y}
        for i, t in enumerate(p.get("targets", [])):
            t["refId"] = t["refId"] or chr(ord("A") + i)
        out.append(p)
        x += w
        line_h = max(line_h, h)
        if p["type"] == "row":
            y, x, line_h = y + 1, 0, 0
    return out


GREEN_RED = [("green", None), ("orange", 0.01), ("red", 0.05)]
FULL = [("green", None), ("orange", 0.8), ("red", 0.9)]
RUNNING = [{"type": "value", "options": {"0": {"text": "Down", "color": "red", "index": 0}}},
           {"type": "range", "options": {"from": 1, "to": 1000, "result": {"text": "Running", "color": "green", "index": 1}}}]

ANNOTATIONS = [
    {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True,
     "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard"},
    {"name": "Containers started", "datasource": PROM, "enable": True, "hide": False, "iconColor": "purple", "step": "1m",
     "expr": by_component(f"(time() - container_start_time_seconds{{{APP}}}) < 120"), "titleFormat": "{{container}} started",
     "textFormat": "{{pod}}: a rollout, a restart or a crash", "useValueForTime": False},
]
LINKS = [
    {"title": "Embeddings (bge-m3)", "type": "link", "url": "/grafana/d/platform-embeddings", "icon": "dashboard",
     "tooltip": "bge-m3 in detail", "asDropdown": False, "includeVars": False, "keepTime": True, "targetBlank": False, "tags": []},
    {"title": "moks-rag on GitHub", "type": "link", "url": REPO, "icon": "external link",
     "tooltip": "How the platform is deployed", "asDropdown": False, "includeVars": False, "keepTime": False,
     "targetBlank": True, "tags": []},
]


def dashboard(uid: str, title: str, desc: str, panels: list[dict[str, Any]], *, tags: list[str],
              variables: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "uid": uid, "title": title, "description": desc, "tags": tags, "editable": False, "graphTooltip": 1,
        "timezone": "browser", "time": {"from": "now-24h", "to": "now"}, "refresh": "1m", "schemaVersion": 41,
        "fiscalYearStartMonth": 0, "liveNow": False, "timepicker": {}, "weekStart": "", "links": LINKS,
        "annotations": {"list": ANNOTATIONS}, "templating": {"list": variables or []}, "panels": layout(panels),
    }


# ---- RAG · Overview -----------------------------------------------------------------------------------------------

def overview() -> dict[str, Any]:
    names = list(COMPONENTS)
    running = " or ".join([by_component(f"count by (container) (container_memory_working_set_bytes{{{APP}}})")]
                          + [f'label_replace(vector(0), "container", "{c}", "", "")' for c in names])
    services = f'destination_service_name=~"{"|".join(names)}"'
    p = [
        text("The shared retrieval platform behind jithendar.com's projects: **rag-api** (`/rag`) and **Open WebUI** "
             "(`/chat`) in front of **Qdrant** (vectors), **bge-m3** (embeddings), **docling-serve** (document parsing) "
             "and **LiteLLM** (Claude models). None of them exports metrics yet, so traffic comes from the Istio "
             f"sidecars, resources from cAdvisor and the kubelet, and operations from rag-api's log events. Source: [moks-rag]({REPO}). "
             "One component in detail: [RAG · Components](/grafana/d/rag-components)."),
        row("Now"),
        stat("Components", running, "Main container of each component, from cAdvisor: Running, or Down when it has not "
             "run for 5 minutes. Running is not ready: mk4 has no kube-state-metrics, so readiness is not recorded.",
             mappings=RUNNING, steps=[("red", None), ("green", 1)], w=24, h=3, legend="{{container}}", named=True),
        stat("API requests", f"{requests_in(RAG_API)} or vector(0)",
             "Requests to rag-api in the time range, from any caller: the gateway (jithendar.com/rag), Open WebUI and "
             "services in other namespaces. Kubernetes' probes do not pass the sidecar and are not counted.", decimals=0),
        stat("Chat UI requests", f"{requests_in(OPEN_WEBUI)} or vector(0)",
             "Requests to Open WebUI (jithendar.com/chat) in the time range: pages, assets, its API and WebSocket "
             "upgrades. One chat is many requests; Chat answers below counts the answers.", decimals=0),
        stat("Server errors", server_errors(services), "Share of requests to any component in the time range that got "
             "a 5xx or never reached a pod (caller-side flags UH, UF, UO, NR). 401 and 404 are callers' mistakes and "
             "do not count.", unit="percentunit", steps=GREEN_RED, no_value="No requests"),
        stat("API p95", range_p95(RAG_API), "95th percentile of rag-api's response time "
             "in the time range. A chat answer streams: its time runs to the end of the answer.", unit="ms",
             no_value="No requests"),
        stat("Vector storage", f'max(kubelet_volume_stats_used_bytes{{{KUBELET}, persistentvolumeclaim=~"qdrant-storage-.*"}})',
             "Used space on Qdrant's 15 GiB Hetzner volume (collections sre_knowledge, cms_content, seo_intel). Points "
             "per collection need Qdrant's own /metrics, which Prometheus does not scrape yet.", unit="bytes",
             no_value="No volume"),
        stat("Since last start", f"time() - max(container_start_time_seconds{{{APP}}})", "Time since the most recently "
             "started main container started: a rollout, a restart or a crash. The purple marks on the graphs say "
             "which one.", unit="dtdurations", no_value="Not running"),

        row("Use"),
        *[stat(title, events(event), f"rag-api `{event}` log events in the time range.", decimals=0, w=6, ds=LOKI)
          for event, title in OPERATIONS.items()],
        ts("Operations", [q(f'sum by (event) (count_over_time({RAG_API_LOGS} | logfmt | event!~"http_request|application_log" [$__interval]))',
                            "{{event}}", ds=LOKI)],
           "rag-api's operations per bar, from its log events: ingests, queries, chat answers (each one a retrieval "
           "and a Claude call), deletes, and openai_list_models, Open WebUI asking which models exist.",
           bars=True, interval="15m", calcs=["sum"], ds=LOKI),
        ts("Requests/s from outside rag", [q(rate("istio_requests_total", sel(MESH_IN, 'source_workload_namespace!="rag"'),
                                                "source_workload_namespace, source_workload, destination_service_name"),
                                           "{{source_workload_namespace}}/{{source_workload}} → {{destination_service_name}}")],
           "Who calls the platform: the gateway for jithendar.com/rag and /chat, and services in other namespaces "
           "once they connect (moks-sre, moks-cms, seo-agent). Counted by the receiving sidecar.", unit="reqps"),

        row("Inside rag"),
        ts("Calls between components", [q(rate("istio_requests_total", sel(MESH_IN, 'source_workload_namespace="rag"'),
                                              "source_workload, destination_service_name"),
                                         "{{source_workload}} → {{destination_service_name}}")],
           "Open WebUI → rag-api for chats; rag-api → bge-m3 (embed), Qdrant (search and store), docling-serve "
           "(parse files) and LiteLLM (answer). rag-api → Qdrant never drops to zero: rag-api's readiness probe checks "
           "Qdrant every 10 s.", unit="reqps"),
        ts("Latency p95 by hop", [q(quantile(0.95, MESH_IN, "source_workload, destination_service_name"),
                                    "{{source_workload}} → {{destination_service_name}}")],
           "95th percentile per caller and component, at the receiving sidecar. WebSocket upgrades (101) are left "
           "out: they last as long as the connection.", unit="ms"),
        ts("Responses by code", [q(rate("istio_requests_total", MESH_IN, "destination_service_name, response_code"),
                                   "{{destination_service_name}} {{response_code}}"),
                                 q(rate("istio_requests_total", UNDELIVERED, "destination_service_name, response_code, response_flags"),
                                   "not delivered: {{destination_service_name}} {{response_code}} {{response_flags}}")],
           "Response codes per component, plus requests a caller could not get to a pod (UH: no healthy pod, UF: "
           "connection failed, UO: overflow, NR: no route). 302 and 401 at Open WebUI are its sign-in.", unit="reqps"),
        ts("Traffic to the internet", [q(rate("istio_tcp_sent_bytes_total", INTERNET, "source_workload"), "{{source_workload}} downloaded"),
                                       q(rate("istio_tcp_received_bytes_total", INTERNET, "source_workload"), "{{source_workload}} uploaded")],
           "Bytes over connections that leave the mesh: LiteLLM to the Claude API, bge-m3 fetching its 1.16 GB model "
           "on a new volume, and whatever else a component downloads. TLS, so no requests or codes. Istio calls the "
           "response bytes sent, so downloaded is istio_tcp_sent_bytes_total.", unit="Bps"),

        row("Resources"),
        bars("Memory of limit", [q(by_component(f"max by (container) (container_memory_working_set_bytes{{{APP}}}) / "
                                                f"max by (container) (container_spec_memory_limit_bytes{{{APP}}} > 0)"),
                                   "{{container}}")],
             "Working set of each main container over its memory limit. The kernel OOM-kills a container at 100%.",
             steps=FULL),
        ts("CPU by component", [q(by_component(rate("container_cpu_usage_seconds_total", APP, "container")), "{{container}}")],
           "Cores used by each main container. bge-m3 has no CPU limit: Ollama sizes its threads to the node and uses "
           "up to about 3.5 of its 4 cores while it embeds.", w=8),
        ts("Memory by component", [q(by_component(f"max by (container) (container_memory_working_set_bytes{{{APP}}})"), "{{container}}")],
           "Working set of each main container. bge-m3 holds its model (about 1.25 GiB) all the time.", unit="bytes", w=8),
        ts("CPU throttled", [q(by_component(f"{rate('container_cpu_cfs_throttled_periods_total', APP, 'container')} / "
                                            f"{rate('container_cpu_cfs_periods_total', APP, 'container')}"), "{{container}}")],
           "Share of scheduling periods in which a container hit its CPU limit and waited. Slow answers with high "
           "throttling need a higher limit, not a faster model. bge-m3 has no limit and no series.",
           unit="percentunit", w=8),
        bars("Volumes", [q(f"max by (persistentvolumeclaim) (kubelet_volume_stats_used_bytes{{{KUBELET}}}) / "
                           f"max by (persistentvolumeclaim) (kubelet_volume_stats_capacity_bytes{{{KUBELET}}})",
                           "{{persistentvolumeclaim}}")],
             "Used share of each volume: Qdrant's collections (15 GiB) and bge-m3's model (10 GiB). Open WebUI's 2 GiB "
             "volume is on local-path storage, which the kubelet does not measure.", steps=FULL),
        ts("Network by component", [q(f"sum by (component) ({pod_component(f'rate(container_network_receive_bytes_total{{{CADVISOR}}}[$__rate_interval])')})",
                                      "{{component}} received"),
                                    q(f"sum by (component) ({pod_component(f'rate(container_network_transmit_bytes_total{{{CADVISOR}}}[$__rate_interval])')})",
                                      "{{component}} sent")],
           "Bytes in and out of each pod, sidecar included. Every pod in the mesh receives about 8 to 15 kB/s even "
           "when idle (rag or not), so a component's own traffic is the rise above that.", unit="Bps", w=8),
    ]
    return dashboard("rag-overview", "RAG · Overview", "The shared RAG platform in namespace rag (mrjithendar/moks-rag): "
                     "what runs, who calls it, what it did and what it uses.", p,
                     tags=["rag-platform", "qdrant", "bge-m3", "litellm", "open-webui"])


# ---- RAG · Components ---------------------------------------------------------------------------------------------

COMPONENT = {"name": "component", "label": "Component", "type": "custom", "hide": 0, "multi": False, "includeAll": False,
             "query": ",".join(COMPONENTS), "current": {"text": "rag-api", "value": "rag-api", "selected": True},
             "options": [{"text": c, "value": c, "selected": c == "rag-api"} for c in COMPONENTS]}
POD = 'pod=~"${component}-.*"'
MAIN = sel(CADVISOR, POD, 'container!=""', 'container!="POD"', 'container!="istio-proxy"')
SERVICE = 'destination_service_name="${component}"'
OUT_CALLS = sel('job="envoy-stats", reporter="source", source_workload_namespace="rag"', 'source_workload="${component}"')


def components() -> dict[str, Any]:
    pod_logs = '{namespace="rag", pod=~"${component}-.*", container!~"istio-proxy|istio-validation"}'
    p = [
        row("Now"),
        stat("Containers running", f"count(container_memory_working_set_bytes{{{MAIN}}}) or vector(0)",
             "Main containers of this component that cAdvisor sees: 1, or 2 during a rollout. Running is not ready: "
             "mk4 has no kube-state-metrics.", mappings=[RUNNING[0]], steps=[("red", None), ("green", 1)]),
        stat("Uptime", f"time() - max(container_start_time_seconds{{{MAIN}}})", "Since the main container last started: "
             "a rollout, a restart or a crash.", unit="dtdurations", no_value="Not running"),
        stat("Requests in", f"{requests_in(SERVICE)} or vector(0)", "Requests to this component in the time range: what "
             "its sidecar answered plus what callers could not deliver, first requests of a new caller or pod included.",
             decimals=0),
        stat("Server errors", server_errors(SERVICE), "Share of those requests that got a 5xx or never reached the pod.",
             unit="percentunit", steps=GREEN_RED, no_value="No requests"),
        stat("p95 in", range_p95(SERVICE), "95th percentile of this component's response time in the time range, "
             "WebSocket upgrades left out.", unit="ms", no_value="No requests"),
        stat("Memory of limit", f"max(container_memory_working_set_bytes{{{MAIN}}}) / max(container_spec_memory_limit_bytes{{{MAIN}}} > 0)",
             "Working set of the main container over its memory limit; the kernel OOM-kills it at 100%.",
             unit="percentunit", steps=FULL, no_value="Not running"),

        row("Traffic in"),
        ts("Requests/s in, by caller", [q(rate("istio_requests_total", sel(MESH_IN, SERVICE), "source_workload_namespace, source_workload"),
                                         "{{source_workload_namespace}}/{{source_workload}}")],
           "Requests this component answered, per caller, counted by its own sidecar. Qdrant: rag-api's readiness "
           "probe alone is one request every 10 s.", unit="reqps", w=8),
        ts("Responses by code", [q(rate("istio_requests_total", sel(MESH_IN, SERVICE), "response_code, response_flags"),
                                   "{{response_code}} {{response_flags}}"),
                                 q(rate("istio_requests_total", sel(UNDELIVERED, SERVICE), "response_code, response_flags"),
                                   "not delivered {{response_code}} {{response_flags}}")],
           "Code and Envoy flags at the sidecar (DC: the caller hung up; code 0: no response sent), plus requests a "
           "caller's sidecar could not get to the pod (UH, UF, UO, NR).", unit="reqps", w=8),
        ts("Latency in, by caller", [q(quantile(0.5, sel(MESH_IN, SERVICE), "source_workload"), "p50 {{source_workload}}"),
                                     q(quantile(0.95, sel(MESH_IN, SERVICE), "source_workload"), "p95 {{source_workload}}")],
           "Median and 95th percentile per caller, WebSocket upgrades left out.", unit="ms", w=8),

        row("Calls out"),
        ts("Requests/s out, by destination", [q(rate("istio_requests_total", OUT_CALLS, "destination_service_name, response_code"),
                                               "{{destination_service_name}} {{response_code}}")],
           "HTTP requests this component made, counted by its own sidecar, so failed deliveries are included. "
           "Empty for components that only answer.", unit="reqps", w=8),
        ts("Latency out p95, by destination", [q(quantile(0.95, OUT_CALLS, "destination_service_name"), "{{destination_service_name}}")],
           "95th percentile of the requests this component made, as its sidecar timed them: the callee's time plus "
           "the network.", unit="ms", w=8),
        ts("Traffic to the internet", [q(rate("istio_tcp_sent_bytes_total", sel(INTERNET, 'source_workload="${component}"')), "downloaded"),
                                       q(rate("istio_tcp_received_bytes_total", sel(INTERNET, 'source_workload="${component}"')), "uploaded")],
           "Bytes over connections that leave the mesh: LiteLLM to the Claude API, bge-m3's model download. TLS, so "
           "no requests or codes.", unit="Bps", w=8),

        row("Resources"),
        ts("CPU", [q(rate("container_cpu_usage_seconds_total", sel(CADVISOR, POD, 'container!=""', 'container!="POD"'), "container"), "{{container}}"),
                   q(f"max by (container) (container_spec_cpu_quota{{{MAIN}}} / container_spec_cpu_period{{{MAIN}}})", "limit {{container}}")],
           "Cores used by the main container and its Istio sidecar, and the main container's limit (none for bge-m3).",
           w=8),
        ts("CPU throttled", [q(f"{rate('container_cpu_cfs_throttled_periods_total', MAIN)} / {rate('container_cpu_cfs_periods_total', MAIN)}", "throttled")],
           "Share of scheduling periods in which the main container hit its CPU limit and had to wait.",
           unit="percentunit", w=8, max_=1),
        ts("CPU waiting", [q(f"max(rate(container_pressure_cpu_waiting_seconds_total{{{MAIN}}}[$__rate_interval]))", "waiting")],
           "Share of time the main container had work but no CPU (pressure stall information): throttling, or a "
           "node busy with other pods.", unit="percentunit", w=8),
        ts("Memory", [q(f"max(container_memory_working_set_bytes{{{MAIN}}})", "working set"),
                      q(f"max(container_memory_rss{{{MAIN}}})", "resident"),
                      q(f"max(container_spec_memory_limit_bytes{{{MAIN}}} > 0)", "limit")],
           "Working set (what counts against the limit) and resident memory of the main container.", unit="bytes", w=8),
        ts("Network", [q(rate("container_network_receive_bytes_total", sel(CADVISOR, POD)), "received"),
                       q(rate("container_network_transmit_bytes_total", sel(CADVISOR, POD)), "sent")],
           "Bytes in and out of the pod, sidecar included.", unit="Bps", w=8),
        ts("Volume", [q(f'max by (persistentvolumeclaim) (kubelet_volume_stats_used_bytes{{{KUBELET}, persistentvolumeclaim=~".*${{component}}.*"}})',
                        "used {{persistentvolumeclaim}}"),
                      q(f'max by (persistentvolumeclaim) (kubelet_volume_stats_capacity_bytes{{{KUBELET}, persistentvolumeclaim=~".*${{component}}.*"}})',
                        "capacity {{persistentvolumeclaim}}")],
           "The component's volume: Qdrant's collections and bge-m3's model. rag-api, docling-serve and LiteLLM keep "
           "no data; Open WebUI's volume is on local-path storage, which the kubelet does not measure.", unit="bytes", w=8),

        row("Logs"),
        ts("Log lines by event", [q(f"sum by (container, event) (count_over_time({pod_logs} | logfmt [$__interval]))",
                                    "{{container}} {{event}}", ds=LOKI)],
           "Log lines per bar. Alloy stores only an allowlisted summary of each line (no messages, paths or user "
           "data): plain-text lines become application_log, and rag-api's JSON events keep their names.",
           bars=True, interval="15m", calcs=["sum"], ds=LOKI, w=24, h=7),
    ]
    return dashboard("rag-components", "RAG · Components", "One component of the RAG platform in namespace rag: its "
                     "traffic in and out, resources and logs.", p, tags=["rag-platform", "istio"], variables=[COMPONENT])


DASHBOARDS = {"rag-overview.json": overview, "rag-components.json": components}


def render() -> dict[str, str]:
    return {name: dump(standardize(build(), "public", "RAG")) for name, build in DASHBOARDS.items()}


def main(argv: list[str]) -> int:
    files = render()
    if "--check" in argv:
        stale = [n for n, body in files.items() if not (OUT / n).exists() or (OUT / n).read_text(encoding="utf-8") != body]
        extra = sorted(p.name for p in OUT.glob("*.json") if p.name not in files)
        for n in stale:
            print(f"{OUT / n} differs from build.py; run python3 generators/rag/build.py")
        for n in extra:
            print(f"{OUT / n} is not built by build.py")
        return 1 if stale or extra else 0
    OUT.mkdir(exist_ok=True)
    for n, body in files.items():
        (OUT / n).write_text(body, encoding="utf-8")
    print(f"wrote {len(files)} dashboards to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
