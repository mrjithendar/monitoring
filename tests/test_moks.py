"""The MOKS dashboards (generators/moks/build.py): moved from moks-sre-agent's backend/tests/test_grafana_dashboards.py and
k8s-deployments' tests/test_moks_monitoring.py. Whether each metric has data on mk4: scripts/audit_live.py."""
import importlib.util
import re

import pytest

from conftest import ROOT, exprs

FILES = ["moks-crewai.json", "moks-knowledge.json", "moks-llm.json", "moks-ml.json", "moks-overview.json", "moks-platform.json"]


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location("moks_build", ROOT / "generators/moks/build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_folder_has_the_six_dashboards_build_py_writes(build):
    assert sorted(build.render()) == FILES
    assert sorted(p.name for p in (ROOT / "dashboards/public/MOKS").glob("*.json")) == FILES


@pytest.mark.parametrize("name", FILES)
def test_moks_dashboards_read_the_moks_job(board, name):
    d = board(name[:-5])
    assert d["title"].startswith("MOKS · ") and "moks" in d["tags"]
    # Every query names its job: never another cluster's remote-written series.
    assert all('job="moks"' in e or 'job="otel-collector"' in e or e.startswith("count(ALERTS{") for e in exprs(d)), name


@pytest.mark.parametrize("name", FILES)
def test_every_panel_says_what_it_shows(board, name):
    d = board(name[:-5])
    for p in d["panels"]:
        if p["type"] not in ("row", "text"):
            assert p.get("description"), f"{name}: {p['title']!r} has no description"
            assert all(t.get("refId") for t in p["targets"]), (name, p["title"])
    assert {v["name"] for v in d["templating"]["list"]} >= {"datasource", "logs"}
    if any("$component" in e for e in exprs(d)):
        # ".*" also matches series without the label.
        assert any(v["name"] == "component" and v["allValue"] == ".*" for v in d["templating"]["list"]), name


@pytest.mark.parametrize("name", FILES)
def test_logs_stay_in_the_moks_namespace(board, name):
    d = board(name[:-5])
    namespace = next(v for v in d["templating"]["list"] if v["name"] == "namespace")
    assert namespace["type"] == "constant" and namespace["query"] == "moks-sre"
    for e in exprs(d, "loki"):
        assert 'namespace="$namespace"' in e, e
