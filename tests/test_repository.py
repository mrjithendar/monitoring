"""The repository as a whole: checks pass, files are formatted and built, and what the deploy applies."""
import json
import re
import subprocess
import sys

import pytest

import dashboards
import render_configmaps
from conftest import ROOT


def run(*args):
    return subprocess.run([sys.executable, *args], cwd=ROOT, text=True, capture_output=True)


@pytest.mark.parametrize("command", [
    ["scripts/check_dashboards.py"],
    ["scripts/format_dashboards.py", "--check"],
    ["generators/moks/build.py", "--check"],
    ["generators/rag/build.py", "--check"],
])
def test_checks_pass(command):
    result = run(*command)
    assert result.returncode == 0, result.stdout + result.stderr


def test_folders_and_orgs():
    found = {(e.org, e.folder) for e in dashboards.entries()}
    assert found == {("public", ""), ("public", "MOKS"), ("public", "Platform"), ("public", "RAG"),
                     ("private", "Jithendar.com"), ("inactive", "RoboShop")}
    # Jithendar.com's dashboards are private; everything else that runs on mk4 is public.
    assert all(e.org == "private" for e in dashboards.entries() if e.folder == "Jithendar.com")
    assert all(e.org == "public" for e in dashboards.entries() if e.folder in ("MOKS", "Platform", "RAG"))


def test_folder_tags_are_unique_to_their_folder(boards):
    # A folder's dropdown lists every dashboard with its tag, wherever it lives. The landing page (no folder) is in
    # the Platform dropdown on purpose.
    for e in dashboards.entries():
        for folder, tag in dashboards.FOLDER_TAGS.items():
            if e.folder and folder != e.folder:
                assert tag not in boards[e.path.stem]["tags"], (e.rel, tag)


def test_the_landing_page_links_every_platform_and_rag_dashboard(board):
    home = board(dashboards.HOME_UID)
    text = next(p["options"]["content"] for p in home["panels"] if p["type"] == "text")
    linked = {e.path.stem for e in dashboards.entries() if e.folder in ("Platform", "RAG")}
    assert linked <= set(re.findall(r"/grafana/d/([\w-]+)", text))
    assert "/grafana/d/moks-overview" in text and "https://jithendar.com/" in text


def test_every_public_dashboard_links_home_and_the_site(boards):
    for e in dashboards.entries():
        d = boards[e.path.stem]
        urls = [link.get("url") for link in d["links"]]
        assert dashboards.SITE in urls, e.rel
        if e.org == "public" and e.folder:
            assert f"/grafana/d/{dashboards.HOME_UID}" in urls, e.rel
        if e.folder:
            assert dashboards.FOLDER_TAGS[e.folder] in d["tags"], e.rel


def test_no_public_dashboard_reads_traces(boards):
    for e in dashboards.entries():
        if e.org == "public":
            assert '"uid": "tempo"' not in json.dumps(boards[e.path.stem]), e.rel  # check_dashboards.py checks types too


def test_render_one_configmap_per_deployed_dashboard():
    items = render_configmaps.configmaps()
    deployed = [e for e in dashboards.entries() if e.org in dashboards.DEPLOYED]
    assert len(items) == len(deployed)
    names = [i["metadata"]["name"] for i in items]
    assert len(set(names)) == len(names)
    for item, e in zip(items, deployed):
        meta = item["metadata"]
        assert meta["namespace"] == "grafana-dashboards"
        assert meta["labels"]["grafana_dashboard"] == "1"
        assert meta["labels"]["app.kubernetes.io/managed-by"] == render_configmaps.MANAGED_BY
        assert meta["annotations"]["grafana_folder"] == (f"{e.org}/{e.folder}" if e.folder else e.org)
        (key, body), = item["data"].items()
        assert key == f"{e.path.stem}.json" and json.loads(body)["uid"] == e.path.stem
        assert len(json.dumps(item)) < 1024 * 1024
    assert not [n for n in names if "roboshop" in n]  # inactive: not deployed


def test_render_output_is_a_kubernetes_list():
    out = run("scripts/render_configmaps.py")
    assert out.returncode == 0
    doc = json.loads(out.stdout)
    assert doc["kind"] == "List" and doc["items"]
