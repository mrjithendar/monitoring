# monitoring

The Grafana dashboards of **mk4**, the k3s cluster on Hetzner (four arm64 nodes) that runs
[jithendar.com](https://jithendar.com/) and its projects. Every dashboard for every project lives here and reaches
Grafana through one workflow.

**Live:** https://jithendar.com/grafana/d/live-infrastructure (no sign-in; Cloudflare asks you to confirm you are human).

## Layout

| Path | Grafana | Who sees it |
|---|---|---|
| `dashboards/public/live-infrastructure.json` | org **Public**, General: the landing page | anyone |
| `dashboards/public/Platform/` | org Public, folder Platform: cluster, Istio, PostgreSQL, Valkey, runners, embeddings, observability stack | anyone |
| `dashboards/public/MOKS/` | org Public, folder MOKS: the MOKS SRE agent (written by `generators/moks/build.py`) | anyone |
| `dashboards/private/Jithendar.com/` | main org, folder Jithendar.com | signed in only |
| `dashboards/inactive/RoboShop/` | not deployed: RoboShop does not run on mk4 today | nobody |

A dashboard's directory decides its org and folder, and its file name is its uid (`<uid>.json`), which also gives its
URL: `/grafana/d/<uid>`. Keep uids stable once a dashboard is public: links to it live in articles and posts.

## How a dashboard reaches Grafana

```
dashboards/<org>/<Folder>/<uid>.json
  └─ deploy.yml (push to main) ─ render_configmaps.py ─▶ ConfigMap dashboard-<uid>, namespace grafana-dashboards
                                                         label grafana_dashboard=1, annotation grafana_folder=<org>/<Folder>
       └─ Grafana's dashboard sidecar ─▶ /tmp/dashboards/<org>/<Folder>/<uid>.json
            └─ file provider "public" (org Public) or "private" (main org) ─▶ folder <Folder>, read-only
```

Grafana picks up a change within a minute, without a restart. Deleting a file here deletes its ConfigMap, and the
dashboard goes too. The Grafana side (sidecar, providers, the namespace and the deploy's ServiceAccount) is in
[k8s-deployments](https://github.com/mrjithendar/k8s-deployments): `minikube-infrastructure/grafana.tf` and
`deploy/minikube/observability/grafana/values.yaml`.

## Data sources

| uid | Type | Public org | Main org |
|---|---|---|---|
| `roboshop-prometheus` | Prometheus | yes | yes |
| `roboshop-loki` | Loki | yes | yes |
| `jaeger` | Jaeger | no | yes |

Anyone who opens the public Grafana can run their own queries against Prometheus and Loki through Grafana's API, not
only the dashboards' queries (Grafana OSS cannot limit that). Loki holds the logs of every namespace, jithendar.com's
(with visitor addresses) included. Keeping the Jithendar.com dashboards private hides them, not those logs.

## Changing a dashboard

1. Edit it in Grafana (a copy: provisioned dashboards are read-only), then **Export → Export as JSON** with
   "Export the dashboard to use in another instance" off, so data sources keep their uids.
2. Save it as `dashboards/<org>/<Folder>/<uid>.json`. A new folder also needs a tag in `FOLDER_TAGS`
   (`scripts/dashboards.py`).
3. MOKS: edit `generators/moks/build.py`, never its JSON, then `python3 generators/moks/build.py`.
4. Run:

   ```bash
   python3 scripts/format_dashboards.py    # the shared header: links, tags, read-only, one crosshair
   python3 scripts/check_dashboards.py     # uids, grid, data sources per org, variables, links
   python3 -m pytest -q tests              # needs pytest and PyYAML; k8s-deployments beside this checkout for some
   ```

5. Open a pull request. `check.yml` runs the same; merging deploys.

### Does it show data?

Static checks cannot tell. With port-forwards to mk4, `scripts/audit_live.py` runs every query against the live
Prometheus and Loki and lists the panels that error, read a metric Prometheus never stored, or return nothing:

```bash
kubectl -n monitoring port-forward deploy/prometheus 19090:9090 &
kubectl -n monitoring port-forward svc/loki 13100:3100 &
python3 scripts/audit_live.py               # or: python3 scripts/audit_live.py platform-redis
```

Empty is not always wrong: a panel of failures is empty while nothing fails, and a counter appears only after its
first event.

## Deploying

`.github/workflows/deploy.yml` runs on every push to `main` that touches dashboards, generators or scripts, and by
hand (Actions → Deploy dashboards → Run workflow). It uses ServiceAccount
`grafana-dashboards/github-dashboards-deployer`, which may manage ConfigMaps in that namespace and nothing else.

One-time setup, after k8s-deployments has created that ServiceAccount:

```bash
scripts/setup-ci-access.sh       # current kubectl context; KUBE_CONTEXT=<name> to choose another
```

It checks the token's permissions, creates the GitHub environment `mk4` (deployments from `main` only) and stores
`MK4_API_SERVER`, `MK4_CA_DATA` (variables) and `MK4_DASHBOARDS_TOKEN` (environment secret). Until then the workflow
checks and renders, then stops with a notice.

## History

Until 2026-11-24 the dashboards lived in three repositories: Jithendar.com's in `jithendar.com/grafana/`, MOKS's in
`moks-sre-agent/deploy/grafana/` (copied by hand into k8s-deployments), and Platform and RoboShop's in
`k8s-deployments/deploy/minikube/observability/grafana/dashboards/`. Terraform rendered them into four ConfigMaps
and Grafana mounted each one.
