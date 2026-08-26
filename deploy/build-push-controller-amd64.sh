#!/usr/bin/env bash
# Build and push sumo-k8-controller for EKS (linux/amd64). Usage:
#   ./deploy/build-push-controller-amd64.sh [tag]
set -euo pipefail

REGISTRY="154794777636.dkr.ecr.eu-central-2.amazonaws.com"
IMAGE="${REGISTRY}/sumo-k8-controller"
TAG="${1:-$(date +%Y%m%d%H%M%S)-amd64}"

aws ecr get-login-password --region eu-central-2 \
  | docker login --username AWS --password-stdin "${REGISTRY}"

docker build --platform linux/amd64 -t "${IMAGE}:${TAG}" .
docker push "${IMAGE}:${TAG}"
echo "Pushed ${IMAGE}:${TAG}"
echo "Deploy: kubectl set image deployment/sumo-k8-controller -n sumo-k8 app=${IMAGE}:${TAG}"
