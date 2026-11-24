#!/usr/bin/env python3
"""Renders the deployed dashboards (public/ and private/) as one ConfigMap each, for kubectl apply.

    python3 scripts/render_configmaps.py > dashboards.json     # a v1 List, JSON
    python3 scripts/render_configmaps.py --names               # the ConfigMap names, one per line

Each ConfigMap lives in namespace grafana-dashboards and carries:
  label grafana_dashboard=1             Grafana's dashboard sidecar loads it (k8s-deployments: grafana/values.yaml)
  annotation grafana_folder=<org>/<F>   where the sidecar writes the file; one file provider per org reads public/
                                        (org "Public") or private/ (the main org), folders from the directory names
  label app.kubernetes.io/managed-by    what .github/workflows/deploy.yml prunes: a dashboard deleted here goes
Standard library only.
"""
from __future__ import annotations

import json
import sys

from dashboards import entries

NAMESPACE = "grafana-dashboards"
MANAGED_BY = "mrjithendar-monitoring"
SELECTOR = f"app.kubernetes.io/managed-by={MANAGED_BY}"


def configmaps() -> list[dict]:
    out = []
    for e in entries(deployed_only=True):
        d = e.load()
        out.append({
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": f"dashboard-{d['uid']}",
                "namespace": NAMESPACE,
                "labels": {"grafana_dashboard": "1", "app.kubernetes.io/managed-by": MANAGED_BY,
                           "monitoring.jithendar.com/org": e.org},
                "annotations": {"grafana_folder": e.annotation, "monitoring.jithendar.com/source": e.rel},
            },
            "data": {f"{d['uid']}.json": e.path.read_text(encoding="utf-8")},
        })
    return out


def main(argv: list[str]) -> int:
    items = configmaps()
    if "--names" in argv:
        print("\n".join(i["metadata"]["name"] for i in items))
    else:
        json.dump({"apiVersion": "v1", "kind": "List", "items": items}, sys.stdout, indent=1, ensure_ascii=False)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
