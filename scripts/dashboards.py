"""What every script and test here agrees on: where dashboards live, which Grafana org and folder each one goes to,
which data sources each org has, and the header every dashboard shares. Standard library only.

    dashboards/public/<uid>.json             org "Public" (anyone, after Cloudflare's check), General folder
    dashboards/public/<Folder>/<uid>.json    org "Public", folder <Folder>
    dashboards/private/<Folder>/<uid>.json   the main org (signed-in only), folder <Folder>
    dashboards/inactive/<Folder>/<uid>.json  kept, not deployed (what they show does not run on mk4 today)

The deploy (scripts/render_configmaps.py) annotates each ConfigMap with <org>/<Folder>; Grafana's dashboard sidecar
writes the file there and one file provider per org loads it (k8s-deployments, grafana.tf).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARDS = ROOT / "dashboards"
DEPLOYED = ("public", "private")
ORGS = (*DEPLOYED, "inactive")
HOME_UID = "live-infrastructure"
SITE = "https://jithendar.com/"

# Folder → the tag its dashboards carry (the folder's dropdown link lists dashboards by it). RAG's is not "rag":
# MOKS · Knowledge carries that tag for MOKS's own pgvector retrieval, and would show in RAG's dropdown.
FOLDER_TAGS = {"Platform": "platform", "MOKS": "moks", "RAG": "rag-platform", "RoboShop": "roboshop",
               "Jithendar.com": "jithendar"}

# uid → type of the data sources each org has (k8s-deployments: grafana/values.yaml and grafana.tf).
# Public has no traces: only Jithendar.com's dashboards read them, and they are private. Tempo (uid tempo) replaces
# Jaeger (uid jaeger) once k8s-deployments' var.tempo is set (minikube-infrastructure/tempo.tf).
DATASOURCES = {
    "public": {"roboshop-prometheus": "prometheus", "roboshop-loki": "loki"},
    "private": {"roboshop-prometheus": "prometheus", "roboshop-loki": "loki", "tempo": "tempo"},
    "inactive": {"roboshop-prometheus": "prometheus", "roboshop-loki": "loki", "tempo": "tempo"},
}
# Data source variables the MOKS dashboards use, by the plugin type they select.
DATASOURCE_VARIABLES = {"${datasource}": "prometheus", "$datasource": "prometheus", "${logs}": "loki", "$logs": "loki"}


@dataclass
class Entry:
    path: Path
    org: str     # public, private or inactive
    folder: str  # "" for the General folder

    @property
    def rel(self) -> str:
        return str(self.path.relative_to(ROOT))

    @property
    def annotation(self) -> str:
        """Where the sidecar writes the file, under its dashboards folder."""
        return f"{self.org}/{self.folder}" if self.folder else self.org

    def load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))


def entries(*, deployed_only: bool = False) -> list[Entry]:
    out = []
    for org in DEPLOYED if deployed_only else ORGS:
        base = DASHBOARDS / org
        for path in sorted(base.rglob("*.json")):
            rel = path.relative_to(base)
            out.append(Entry(path, org, rel.parts[0] if len(rel.parts) > 1 else ""))
    return out


def panels(dashboard: dict):
    """Every panel, including those inside collapsed rows."""
    for panel in dashboard.get("panels", []):
        yield panel
        yield from panel.get("panels", [])


def dump(dashboard: dict) -> str:
    return json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n"


def nav_links(org: str, folder: str) -> list[dict]:
    """The links at the top right of every dashboard: back to the landing page, the folder's other dashboards, the site."""
    links = []
    if org == "public" and folder:
        links.append({"title": "Live infrastructure", "type": "link", "url": f"/grafana/d/{HOME_UID}", "icon": "dashboard",
                      "tooltip": "All public dashboards", "asDropdown": False, "includeVars": False, "keepTime": False,
                      "targetBlank": False, "tags": []})
    if folder:
        links.append({"title": f"{folder} dashboards", "type": "dashboards", "tags": [FOLDER_TAGS[folder]], "asDropdown": True,
                      "includeVars": False, "keepTime": True, "targetBlank": False, "icon": "external link", "tooltip": "",
                      "url": ""})
    links.append({"title": "jithendar.com", "type": "link", "url": SITE, "icon": "external link",
                  "tooltip": "Articles about how this is built", "asDropdown": False, "includeVars": False,
                  "keepTime": False, "targetBlank": True, "tags": []})
    return links


def standardize(dashboard: dict, org: str, folder: str) -> dict:
    """The header every dashboard shares. Keeps the dashboard's own links that point somewhere else."""
    d = dict(dashboard)
    d["id"] = None
    for key in ("version", "iteration"):
        d.pop(key, None)
    d["editable"] = False
    d["graphTooltip"] = 1  # one crosshair across panels
    tags = list(d.get("tags") or [])
    if folder:
        tag = FOLDER_TAGS[folder]
        tags = [tag] + [t for t in tags if t != tag]
    d["tags"] = tags
    standard = nav_links(org, folder)
    keep = [link for link in d.get("links") or []
            if link.get("type") != "dashboards" and link.get("url") not in {s.get("url") for s in standard}]
    d["links"] = standard + keep
    return d
