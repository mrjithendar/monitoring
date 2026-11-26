"""The RAG platform's dashboards (generators/rag/build.py), for mrjithendar/moks-rag in namespace rag. Whether each query
has data on mk4: scripts/audit_live.py rag."""
import importlib.util
import re

import pytest

from conftest import ROOT, exprs
from test_platform import SELECTOR, UNDELIVERED, counts_new_series, flat

FILES = ["rag-components.json", "rag-overview.json"]
PROM = {"type": "prometheus", "uid": "roboshop-prometheus"}
LOKI = {"type": "loki", "uid": "roboshop-loki"}


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location("rag_build", ROOT / "generators/rag/build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def concrete(expr):
    """SELECTOR stops at the first }, and ${component} has one: read the queries for one component."""
    return expr.replace("${component}", "rag-api")


def queries(d, kind="prometheus"):
    return [concrete(e) for e in exprs(d, kind)]


def stats(d):
    return {p["title"]: concrete(p["targets"][0]["expr"]) for p in d["panels"] if p["type"] == "stat"}


def test_the_folder_has_the_dashboards_build_py_writes(build):
    assert sorted(build.render()) == FILES
    assert sorted(p.name for p in (ROOT / "dashboards/public/RAG").glob("*.json")) == FILES


@pytest.mark.parametrize("name", FILES)
def test_every_panel_says_what_it_shows(board, name):
    d = board(name[:-5])
    assert d["title"].startswith("RAG · ") and d["tags"][0] == "rag-platform"
    for p in d["panels"]:
        if p["type"] not in ("row", "text"):
            assert p.get("description"), f"{name}: {p['title']!r} has no description"
            assert p["targets"] and all(t["datasource"] in (PROM, LOKI) for t in p["targets"]), (name, p["title"])


@pytest.mark.parametrize("name", FILES)
def test_every_query_stays_in_namespace_rag(board, name):
    d = board(name[:-5])
    for e in queries(d):
        if "istio_" in e:
            for metric, labels in SELECTOR.findall(e):
                assert 'job="envoy-stats"' in labels, labels
                assert ('destination_service_namespace="rag"' in labels or 'source_workload_namespace="rag"' in labels), labels
        else:
            assert re.search(r'job="kubernetes-(cadvisor|kubelet)", namespace="rag"', e), e
    for e in queries(d, "loki"):
        assert e.count('{namespace="rag"') == e.count("{namespace="), e


@pytest.mark.parametrize("name", FILES)
def test_range_stats_count_the_first_requests_of_a_new_series(board, name):
    # Traffic here is sparse: a caller or pod that first appears inside the range is most of it.
    for title, e in stats(board(name[:-5])).items():
        if "istio_" in e and "$__range" in e:
            assert counts_new_series(e, "$__range"), title
            if "histogram_quantile" not in e:
                assert UNDELIVERED in flat(e), title  # requests that never reached a pod count too


def test_server_errors_are_5xx_or_undelivered_and_no_traffic_is_no_value(board):
    for uid in ("rag-overview", "rag-components"):
        e = stats(board(uid))["Server errors"]
        assert e.startswith("(sum(") and ') or vector(0)) / sum(' in e and 'response_code=~"5.."' in e, uid


def test_latency_leaves_out_websocket_upgrades(boards):
    # A WebSocket upgrade (101) is timed for as long as the connection stays open.
    for name in FILES:
        for e in queries(boards[name[:-5]]):
            for metric, labels in SELECTOR.findall(e):
                if metric == "istio_request_duration_milliseconds_bucket":
                    assert 'response_code!="101"' in labels, e


def test_every_component_is_listed_and_selectable(build, board):
    assert set(build.COMPONENTS) == {"rag-api", "open-webui", "qdrant", "bge-m3", "docling-serve", "litellm"}
    variable = next(v for v in board("rag-components")["templating"]["list"] if v["name"] == "component")
    assert variable["type"] == "custom" and variable["query"].split(",") == list(build.COMPONENTS)
    assert variable["current"]["value"] in build.COMPONENTS
    # The overview's status panel lists every component, Down when its container is missing.
    status = stats(board("rag-overview"))["Components"]
    for component, container in build.COMPONENTS.items():
        assert f'label_replace(vector(0), "container", "{component}", "", "")' in status
        assert container in status


def test_the_pod_name_pattern_finds_the_component(build):
    pattern = re.compile(f"^{build.POD_COMPONENT}$")
    pods = {"rag-api-657f6b85cb-c69lk": "rag-api", "qdrant-0": "qdrant", "bge-m3-6c9d87749f-9zkm8": "bge-m3",
            "docling-serve-7c899c694f-x25pd": "docling-serve", "open-webui-6f8c67fc9b-75pjd": "open-webui"}
    for pod, component in pods.items():
        assert pattern.match(pod).group(1) == component, pod


def test_operations_count_rag_api_s_log_events(build, board):
    d = board("rag-overview")
    loki = {p["title"]: p["targets"][0]["expr"] for p in d["panels"] if p.get("targets") and p["targets"][0]["datasource"] == LOKI}
    for event, title in build.OPERATIONS.items():
        assert f'event="{event}"' in loki[title] and loki[title].endswith("or vector(0)"), title
