"""What the dashboards need from mrjithendar/k8s-deployments: Prometheus keeps every series they read, istiod runs the
replicas the control-plane dashboard expects, and Grafana's sidecar loads what scripts/render_configmaps.py writes.

Needs a checkout of k8s-deployments next to this one (CI: .github/workflows/check.yml), or K8S_DEPLOYMENTS_DIR, and
helm; skipped otherwise."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import render_configmaps
from conftest import ROOT, exprs
from test_platform import arc_metrics_read

yaml = pytest.importorskip("yaml")
K8S = Path(os.environ.get("K8S_DEPLOYMENTS_DIR", ROOT.parent / "k8s-deployments"))
pytestmark = [
    pytest.mark.skipif(not (K8S / "deploy/minikube").is_dir(), reason=f"no k8s-deployments checkout at {K8S}"),
    pytest.mark.skipif(not shutil.which("helm"), reason="helm is not installed"),
]


def scrape_jobs():
    out = subprocess.check_output(["helm", "template", "prometheus", str(K8S / "deploy/minikube/observability/prometheus"),
                                   "-n", "monitoring", "-s", "templates/config.yaml"], text=True)
    config = yaml.safe_load(yaml.safe_load(out)["data"]["prometheus.yml"])
    return {job["job_name"]: job for job in config["scrape_configs"]}


def test_every_envoy_stats_metric_read_survives_the_scrape_filter(boards):
    relabels = scrape_jobs()["envoy-stats"]["metric_relabel_configs"]
    by_name = [r for r in relabels if r["source_labels"] == ["__name__"]]
    assert [r["action"] for r in by_name] == ["keep", "drop"]

    def kept(metric):
        keep, drop = (re.fullmatch(r["regex"], metric) for r in by_name)
        return bool(keep) and not drop

    read = set()
    for d in boards.values():
        for expr in exprs(d):
            if 'job="envoy-stats"' in expr:
                read |= set(re.findall(r"\b((?:istio|envoy)_[a-z_]+)\b", expr))
    assert {"istio_requests_total", "istio_request_duration_milliseconds_bucket", "envoy_http_rbac"} <= read
    assert {m for m in read if not kept(m)} == set()


def test_every_arc_metric_read_survives_the_scrape_filter(board):
    (keep,) = scrape_jobs()["arc"]["metric_relabel_configs"]
    assert keep["source_labels"] == ["__name__"] and keep["action"] == "keep"
    names = set()
    for m in arc_metrics_read(board) - {"up"}:
        names |= {f"{m}_{s}" for s in ("bucket", "sum", "count")} if m.endswith("_seconds") else {m}
    assert {n for n in names if not re.fullmatch(keep["regex"], n)} == set()
    # The job metrics' labels in by () clauses exist with listenerMetrics unset (ARC's defaults).
    assert "listenerMetrics" not in yaml.safe_load((K8S / "deploy/minikube/runners/values.yaml").read_text())


def test_the_embeddings_readme_documents_the_vector_size_the_dashboard_divides_by():
    assert "12.8 KB" in (K8S / "deploy/minikube/embeddings/bge-m3/README.md").read_text()


def test_the_control_plane_dashboard_expects_istiod_s_replicas(board):
    replicas = yaml.safe_load((K8S / "deploy/minikube/istio/istiod.yaml").read_text())["pilot"]["replicaCount"]
    up = next(p for p in board("platform-istio-control-plane")["panels"] if p["title"] == "istiod replicas up")
    assert up["fieldConfig"]["defaults"]["thresholds"]["steps"][-1] == {"color": "green", "value": replicas}


def test_the_sidecar_loads_what_the_deploy_writes():
    grafana = yaml.safe_load((K8S / "deploy/minikube/observability/grafana/values.yaml").read_text())
    sidecar = grafana.get("sidecar", {}).get("dashboards", {})
    if not sidecar.get("enabled"):
        pytest.skip("k8s-deployments does not load dashboards through the sidecar yet")
    item = render_configmaps.configmaps()[0]
    assert sidecar["label"] in item["metadata"]["labels"]
    assert str(sidecar.get("labelValue", "")) in ("", item["metadata"]["labels"][sidecar["label"]])
    assert sidecar["searchNamespace"] == render_configmaps.NAMESPACE
    assert sidecar["folderAnnotation"] in item["metadata"]["annotations"]
    paths = {p["options"]["path"] for f in grafana["dashboardProviders"].values() for p in f["providers"]}
    assert {f"{sidecar['folder']}/public", f"{sidecar['folder']}/private"} <= paths
    json.dumps(item)
