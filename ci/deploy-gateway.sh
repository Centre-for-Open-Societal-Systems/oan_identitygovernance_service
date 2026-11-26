#!/usr/bin/env bash
# deploy-gateway.sh - Install or upgrade the APISIX route of central RBAC (chart apisix/k8s) on the oan_dev k3s node
# Called by Jenkins for develop, after the app deploy.
#
# Runs on the deploy agent with the kubeconfig of the deployer ServiceAccount (Jenkins binds it as KUBECONFIG);
# the agent needs helm and kubectl. APISIX and its Ingress Controller are a separate release (namespace
# api6-gateway); this chart only holds the ApisixRoute, which the controller picks up. The namespace needs the
# deployer's access (deployNamespaces in oan-helm's environments/dev/values/oan-cluster-access.yaml).
#
# Optional overrides: GATEWAY_RELEASE, GATEWAY_CHART, GATEWAY_VALUES.
set -euo pipefail

: "${KUBECONFIG:?KUBECONFIG is required (credential oan-dev-kubeconfig)}"
: "${RBAC_NAMESPACE:?RBAC_NAMESPACE is required}"

RELEASE="${GATEWAY_RELEASE:-identitygovernance-gateway}"
# The archive "Package gateway chart" built for this commit (unstashed into built-gateway-chart/), else the chart folder.
PACKAGED=""
if [ -n "${IMMUTABLE_TAG:-}" ] && [ -f "built-gateway-chart/oan-identitygovernance-gateway-${IMMUTABLE_TAG}.tgz" ]; then
  PACKAGED="built-gateway-chart/oan-identitygovernance-gateway-${IMMUTABLE_TAG}.tgz"
fi
CHART="${GATEWAY_CHART:-${PACKAGED:-apisix/k8s}}"
VALUES="${GATEWAY_VALUES:-helm/environments/dev/oan-identitygovernance-gateway.yaml}"
NS="${RBAC_NAMESPACE}"

echo "=== Gateway ${RELEASE}: namespace ${NS} ==="

helm lint "${CHART}" -f "${VALUES}"

if ! helm upgrade --install "${RELEASE}" "${CHART}" --namespace "${NS}" -f "${VALUES}" --wait --timeout 5m; then
  echo "=== helm upgrade failed ==="
  helm -n "${NS}" history "${RELEASE}" --max 3 || true
  exit 1
fi

helm -n "${NS}" status "${RELEASE}"
kubectl -n "${NS}" get apisixroute

echo "=== gateway deploy complete: ${RELEASE} ==="
