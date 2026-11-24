"""The Platform dashboards: moved with them from k8s-deployments (tests/test_embeddings_monitoring.py,
test_istio_monitoring.py, test_runners_monitoring.py, test_postgres.py). What they need from Prometheus's scrape
configuration is checked in test_k8s_deployments.py."""
import re

import pytest

from conftest import exprs

PROM = {"type": "prometheus", "uid": "roboshop-prometheus"}
# Every query names its job: never another cluster's remote-written series (the EKS agent writes to this Prometheus).
JOBS = {
    "platform-istio-mesh": r'job="(envoy-stats|istiod|kubernetes-cadvisor)"',
    "platform-istio-control-plane": r'job="(envoy-stats|istiod|kubernetes-cadvisor)"',
    "platform-github-runners": r'job="(arc|kubernetes-cadvisor)"',
    "platform-github-runner-pods": r'job="(arc|kubernetes-cadvisor)"',
    "platform-postgres": r'job="postgres"|container_',
    "platform-redis": r'job="redis"|container_',
    "platform-kubernetes": r'job="kubernetes-(cadvisor|kubelet)"',
    # Plus the per-job views of Prometheus's own scrape_* series (one per target, whatever the job).
    "platform-observability": r'job="(prometheus|loki|alloy|kubernetes-cadvisor)"|by \(job\) \(scrape_',
}


def targets(d):
    return [t for p in d["panels"] for t in p.get("targets", [])]


@pytest.mark.parametrize("uid,pattern", JOBS.items())
def test_every_query_names_its_job(board, uid, pattern):
    d = board(uid)
    assert d["uid"] == uid and "platform" in d["tags"] and d["title"].startswith("Platform · ")
    ts = targets(d)
    assert ts and all(t["datasource"] == PROM for t in ts if "expr" in t and t["datasource"]["type"] == "prometheus")
    for e in exprs(d):
        assert re.search(pattern, e), (uid, e)


# ---- bge-m3 embeddings ------------------------------------------------------------------------------------------

SELECTOR = re.compile(r"(istio_\w+)\{([^}]*)\}")
UNDELIVERED = 'response_flags=~".*(UH|UF|UO|NR).*"'


def flat(expr):
    return " ".join(expr.split())


def counts_new_series(expr, window):
    """Each Istio counter counts with increase() plus the start value of a series that appears inside the window,
    which increase() alone reads as 0."""
    expr = flat(expr)
    for name, labels in set(SELECTOR.findall(expr)):
        series = f"{name}{{{labels}}}"
        assert f"increase({series}[{window}])" in expr, series
        assert f"min_over_time({series}[{window}]) unless {series} offset {window}" in expr, series
    return True


def test_embeddings_queries_select_bge_m3_only(board):
    for expr in exprs(board("platform-embeddings")):
        if "istio_" in expr:
            for _, labels in SELECTOR.findall(expr):
                assert 'destination_service_name="bge-m3"' in labels, labels
                assert 'destination_service_namespace="embeddings"' in labels and 'job="envoy-stats"' in labels, labels
                # bge-m3's own sidecar; the callers' sidecars only for requests that never reached the pod.
                assert 'reporter="destination"' in labels or ('reporter="source"' in labels and UNDELIVERED in labels), labels
        elif "kubelet_volume_stats" in expr:
            assert 'persistentvolumeclaim="bge-m3-models"' in expr and 'namespace="embeddings"' in expr, expr
        else:
            assert "container_" in expr and 'namespace="embeddings"' in expr and 'pod=~"bge-m3-.*"' in expr, expr


def test_embeddings_stats_count_a_callers_first_request(board):
    d = board("platform-embeddings")
    stats = {p["title"]: p["targets"][0]["expr"] for p in d["panels"] if p["type"] == "stat" and "istio_" in p["targets"][0]["expr"]}
    assert set(stats) == {"Requests", "Failed", "p95 latency"}
    assert all(counts_new_series(e, "$__range") for e in stats.values())
    assert UNDELIVERED in stats["Requests"] and UNDELIVERED in stats["Failed"]
    assert stats["Failed"].startswith("(sum(") and ") or vector(0)) / sum(" in stats["Failed"]  # no failures is 0 %


def test_embeddings_responses_show_requests_that_never_reached_the_pod(board):
    panel = next(p for p in board("platform-embeddings")["panels"] if p["title"] == "Responses by code")
    undelivered = [t for t in panel["targets"] if UNDELIVERED in t["expr"]]
    assert len(undelivered) == 1 and undelivered[0]["legendFormat"].startswith("not delivered")


# ---- Istio --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("uid", ["platform-istio-mesh", "platform-istio-control-plane"])
def test_istio_dashboards(board, uid):
    d = board(uid)
    assert {"platform", "istio"} <= set(d["tags"])
    assert all(v["datasource"]["uid"] == "roboshop-prometheus" for v in d["templating"]["list"])


# ---- GitHub Actions runners (ARC) -----------------------------------------------------------------------------------

RUNNERS = ["platform-github-runners", "platform-github-runner-pods"]
LISTENER_METRICS = {
    "gha_assigned_jobs", "gha_running_jobs", "gha_registered_runners", "gha_busy_runners", "gha_min_runners",
    "gha_max_runners", "gha_desired_runners", "gha_idle_runners", "gha_started_jobs_total",
    "gha_completed_jobs_total", "gha_job_startup_duration_seconds", "gha_job_execution_duration_seconds"}
CONTROLLER_METRICS = {
    f"gha_controller_{m}" for m in ("pending_ephemeral_runners", "running_ephemeral_runners", "succeeded_ephemeral_runners",
                                    "failed_ephemeral_runners", "outdated_ephemeral_runners", "running_listeners")}
JOB_LABELS = {"repository", "organization", "enterprise", "job_name", "event_name", "job_result", "le"}


def arc_metrics_read(board):
    """Metric names (histograms without _bucket/_sum/_count) read from job arc by either dashboard."""
    read = set()
    for uid in RUNNERS:
        d = board(uid)
        for expr in exprs(d) + [v["query"]["query"] for v in d["templating"]["list"]]:
            if 'job="arc"' in expr:
                read |= {re.sub(r"_(bucket|sum|count)$", "", m) for m in re.findall(r'\b([a-z_]+)\{job="arc"', expr)}
    return read


@pytest.mark.parametrize("uid", RUNNERS)
def test_runner_dashboards(board, uid):
    d = board(uid)
    assert {"platform", "github-runners"} <= set(d["tags"])
    assert all(v["datasource"]["uid"] == "roboshop-prometheus" for v in d["templating"]["list"])
    text = "\n".join(exprs(d))
    for v in d["templating"]["list"]:
        assert f'${v["name"]}' in text, v["name"]
    for expr in exprs(d):
        for by in re.findall(r"histogram_quantile\([0-9.]+, sum by \(([^)]*)\) \(increase\([a-z_]+_bucket\{", expr):
            assert "le" in [label.strip() for label in by.split(",")], expr
        assert expr.count("histogram_quantile(") == expr.count("_bucket{"), expr


def test_runner_dashboards_read_only_what_arc_exports(board):
    read = arc_metrics_read(board)
    assert {"gha_running_jobs", "gha_completed_jobs_total", "gha_job_startup_duration_seconds",
            "gha_controller_pending_ephemeral_runners"} <= read
    assert read - {"up"} <= LISTENER_METRICS | CONTROLLER_METRICS | {
        "controller_runtime_reconcile_total", "controller_runtime_reconcile_errors_total", "workqueue_depth"}
    for expr in exprs(board("platform-github-runners")):
        for by in re.findall(r"by \(([^)]*)\) \((?:increase|rate)\(gha_(?:completed|started|job)_", expr):
            assert {label.strip() for label in by.split(",")} <= JOB_LABELS, expr
