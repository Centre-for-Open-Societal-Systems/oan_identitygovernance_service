#!/usr/bin/env bash
# deploy-helm-dev.sh - Install or upgrade the `identitygovernance` Helm release on the oan_dev k3s node
# Called by Jenkins for develop branch deployments, after "Push image"
#
# Runs on the deploy agent with the kubeconfig of the deployer ServiceAccount (Jenkins binds it as
# KUBECONFIG); the agent needs helm and kubectl, no AWS access: the node pulls from ECR with its own IAM role.
# Prerequisites in the release namespace, created outside Git:
# - Secret central-rbac-keycloak-client (key client_secret): secret of the `central-rbac` Keycloak client
# - Secret commons-postgresql (key postgres-password): read by the postgres-init subchart
set -euo pipefail

: "${KUBECONFIG:?KUBECONFIG is required (credential oan-dev-kubeconfig)}"
: "${RBAC_NAMESPACE:?RBAC_NAMESPACE is required}"
: "${IMMUTABLE_TAG:?IMMUTABLE_TAG is required}"
: "${KEYCLOAK_BASE_URL:?KEYCLOAK_BASE_URL is required}"

CHART=helm/oan-identitygovernance-service
VALUES=helm/environments/dev/oan-identitygovernance-service.yaml
NS="${RBAC_NAMESPACE}"
RELEASE=identitygovernance

echo "=== Deploying to oan_dev: namespace ${NS}, image tag ${IMMUTABLE_TAG} ==="

helm repo add openg2p https://openg2p.github.io/openg2p-helm >/dev/null
helm dependency build "${CHART}"

helm upgrade --install "${RELEASE}" "${CHART}" \
  --namespace "${NS}" --create-namespace \
  --values "${VALUES}" \
  --set image.tag="${IMMUTABLE_TAG}" \
  --set global.keycloakBaseUrl="${KEYCLOAK_BASE_URL}" \
  --wait --timeout 10m

kubectl -n "${NS}" rollout status "deployment/${RELEASE}-oan-identitygovernance-service" --timeout=5m
