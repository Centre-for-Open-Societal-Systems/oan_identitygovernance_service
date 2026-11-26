#!/usr/bin/env bash
# deploy-helm-dev.sh - Install or upgrade the `identitygovernance` Helm release on the oan_dev k3s node
# Called by Jenkins for develop branch deployments, after "Package chart"
#
# Runs on the deploy agent with the kubeconfig of the deployer ServiceAccount (Jenkins binds it as
# KUBECONFIG); the agent needs helm and kubectl, no AWS access: the node pulls from ECR with its own IAM role.
# The deployer cannot create namespaces: the release namespace must exist and be listed in deployNamespaces of
# oan-helm's environments/dev/values/oan-cluster-access.yaml.
# Prerequisites in the release namespace, created outside Git:
# - Secret central-rbac-keycloak-client (key client_secret): secret of the `central-rbac` Keycloak client
# - Secret commons-postgresql (key postgres-password): read by the postgres-init subchart
set -euo pipefail

: "${KUBECONFIG:?KUBECONFIG is required (credential oan-dev-kubeconfig)}"
: "${RBAC_NAMESPACE:?RBAC_NAMESPACE is required}"
: "${IMMUTABLE_TAG:?IMMUTABLE_TAG is required}"

# DEPLOY_ENV picks the values: dev (develop) or staging (the staging branch, a promoted chart).
DEPLOY_ENV="${DEPLOY_ENV:-dev}"
CHART_TGZ="built-chart/oan-identitygovernance-service-${IMMUTABLE_TAG}.tgz"
VALUES="helm/environments/${DEPLOY_ENV}/oan-identitygovernance-service.yaml"
NS="${RBAC_NAMESPACE}"
RELEASE=identitygovernance

test -f "${CHART_TGZ}" || { echo "${CHART_TGZ} is missing: run package_chart.py package first" >&2; exit 1; }

echo "=== Deploying to ${DEPLOY_ENV}: namespace ${NS}, image tag ${IMMUTABLE_TAG} ==="

helm upgrade --install "${RELEASE}" "${CHART_TGZ}" \
  --namespace "${NS}" \
  --values "${VALUES}" \
  --set image.tag="${IMMUTABLE_TAG}" \
  --wait --timeout 10m

kubectl -n "${NS}" rollout status "deployment/${RELEASE}-oan-identitygovernance-service" --timeout=5m
