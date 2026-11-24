#!/usr/bin/env bash
# One-time: gives .github/workflows/deploy.yml access to mk4, limited to the dashboard ConfigMaps.
#   1. creates the token Secret of ServiceAccount grafana-dashboards/github-dashboards-deployer (k8s-deployments
#      creates the ServiceAccount and its Role: minikube-infrastructure/grafana.tf; apply that first). The Secret is
#      not Terraform's, so the token never sits in Terraform state;
#   2. checks the token can manage ConfigMaps in grafana-dashboards and nothing else that matters (no Secrets, no other
#      namespace);
#   3. creates the GitHub environment "mk4" (deployable from main only) and stores:
#        MK4_DASHBOARDS_TOKEN   environment secret (mk4)   the ServiceAccount token
#        MK4_API_SERVER         repository variable        cluster API URL from the kubeconfig context
#        MK4_CA_DATA            repository variable        base64 cluster CA
#
#   scripts/setup-ci-access.sh                      # current kubectl context
#   KUBE_CONTEXT=<context> scripts/setup-ci-access.sh
#   SKIP_GH=1 scripts/setup-ci-access.sh            # only check the token; set the GitHub values yourself
#   ROTATE=1 scripts/setup-ci-access.sh             # replace the token (the old one stops working)
#
# The token goes straight into `gh secret set` (stdin) and is never printed. Needs kubectl that can read the Secret in
# grafana-dashboards, and gh signed in with admin on the repository (unless SKIP_GH=1).
# Revoke at any time: kubectl -n grafana-dashboards delete secret github-dashboards-deployer-token
set -euo pipefail

REPO="${GITHUB_REPO:-mrjithendar/monitoring}"
NS="grafana-dashboards"
SA="github-dashboards-deployer"
TEMPLATE=".github/mk4-dashboards-kubeconfig.yaml"
CONTEXT="${KUBE_CONTEXT:-$(kubectl config current-context)}"
k() { kubectl --context "$CONTEXT" "$@"; }

cd "$(dirname -- "${BASH_SOURCE[0]}")/.."

MK4_API_SERVER="$(kubectl config view --raw --minify --context "$CONTEXT" -o jsonpath='{.clusters[0].cluster.server}')"
MK4_CA_DATA="$(kubectl config view --raw --minify --context "$CONTEXT" -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')"
[[ -n "$MK4_CA_DATA" ]] || MK4_CA_DATA="$(base64 < "$(kubectl config view --raw --minify --context "$CONTEXT" -o jsonpath='{.clusters[0].cluster.certificate-authority}')" | tr -d '\n')"
[[ -n "$MK4_API_SERVER" && -n "$MK4_CA_DATA" ]] || { echo "Could not read server/CA of context $CONTEXT." >&2; exit 1; }
case "$MK4_API_SERVER" in
  *127.0.0.1*|*localhost*|*192.168.*) echo "Context $CONTEXT points at $MK4_API_SERVER, which GitHub's runners cannot reach. Use a context with the public k3s API (https://<master IP>:6443)." >&2; exit 1 ;;
esac
if [[ -t 0 ]]; then
  read -r -p "Context $CONTEXT ($MK4_API_SERVER) → repository $REPO. Continue? [y/N] " answer
  [[ "$answer" == [yY] ]] || exit 1
fi

k -n "$NS" get serviceaccount "$SA" >/dev/null 2>&1 || { echo "No ServiceAccount $NS/$SA: apply k8s-deployments (grafana.tf) first." >&2; exit 1; }
if [[ "${ROTATE:-0}" == "1" ]]; then
  echo "→ Deleting the old token"
  k -n "$NS" delete secret "$SA-token" --ignore-not-found
fi
echo "→ Token Secret $NS/$SA-token"
k apply -f - <<YAML
apiVersion: v1
kind: Secret
metadata:
  name: $SA-token
  namespace: $NS
  labels: {app.kubernetes.io/part-of: monitoring}
  annotations:
    kubernetes.io/service-account.name: $SA
type: kubernetes.io/service-account-token
YAML
MK4_DASHBOARDS_TOKEN=""
for _ in $(seq 1 20); do
  MK4_DASHBOARDS_TOKEN="$(k -n "$NS" get secret "$SA-token" -o jsonpath='{.data.token}' 2>/dev/null | base64 -d || true)"
  [[ -n "$MK4_DASHBOARDS_TOKEN" ]] && break
  sleep 1
done
[[ -n "$MK4_DASHBOARDS_TOKEN" ]] || { echo "The token was not issued for $NS/$SA-token." >&2; exit 1; }
export MK4_API_SERVER MK4_CA_DATA MK4_DASHBOARDS_TOKEN

echo "→ Checking what the token may do, through the kubeconfig the workflow builds"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
(umask 077; perl -pe 's/\$\{(MK4_[A-Z_]+)\}/$ENV{$1}/g' "$TEMPLATE" > "$tmp/kubeconfig")
can() { KUBECONFIG="$tmp/kubeconfig" kubectl auth can-i "$@" 2>/dev/null || true; }
for verb in get list create patch update delete; do
  [[ "$(can "$verb" configmaps -n "$NS")" == yes ]] || { echo "The token cannot $verb ConfigMaps in $NS." >&2; exit 1; }
done
[[ "$(can get secrets -n "$NS")" == no ]] || { echo "The token can read Secrets in $NS: refusing, it should not." >&2; exit 1; }
[[ "$(can create configmaps -n monitoring)" == no ]] || { echo "The token can write outside $NS: refusing, it should not." >&2; exit 1; }
echo "  ConfigMaps in $NS only: OK"

if [[ "${SKIP_GH:-0}" == "1" ]]; then
  echo "SKIP_GH=1: set MK4_API_SERVER and MK4_CA_DATA (repository variables) and MK4_DASHBOARDS_TOKEN (secret of environment mk4) in $REPO yourself."
  exit 0
fi

echo "→ GitHub environment mk4 (deployments from main only)"
gh api -X PUT "repos/$REPO/environments/mk4" --input - >/dev/null <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
gh api "repos/$REPO/environments/mk4/deployment-branch-policies" --jq '.branch_policies[].name' | grep -qx main \
  || gh api -X POST "repos/$REPO/environments/mk4/deployment-branch-policies" -f name=main -f type=branch >/dev/null

echo "→ Variables and secret"
gh variable set MK4_API_SERVER --repo "$REPO" --body "$MK4_API_SERVER"
gh variable set MK4_CA_DATA --repo "$REPO" --body "$MK4_CA_DATA"
printf '%s' "$MK4_DASHBOARDS_TOKEN" | gh secret set MK4_DASHBOARDS_TOKEN --repo "$REPO" --env mk4

echo "Done. Deploy now: gh workflow run deploy.yml --repo $REPO --ref main"
