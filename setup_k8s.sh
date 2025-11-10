#!/bin/bash

set -e

echo "Setting up local Kubernetes cluster for SUMO-K8..."

K8S_TOOL=""
K8S_TOOL_VERSION=""

if command -v kind &> /dev/null; then
    K8S_TOOL="kind"
    echo "Found kind: $(kind --version)"
elif command -v minikube &> /dev/null; then
    K8S_TOOL="minikube"
    echo "Found minikube: $(minikube version | head -1)"
elif command -v docker &> /dev/null && docker info &> /dev/null; then
    echo "Docker is available. Installing kind..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install kind
    else
        curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-linux-amd64
        chmod +x ./kind
        sudo mv ./kind /usr/local/bin/kind
    fi
    K8S_TOOL="kind"
else
    echo "Error: No Kubernetes tool found and Docker not available."
    echo "Please install one of:"
    echo "  - kind: https://kind.sigs.k8s.io/docs/user/quick-start/"
    echo "  - minikube: https://minikube.sigs.k8s.io/docs/start/"
    echo "  - Docker Desktop with Kubernetes enabled"
    exit 1
fi

CLUSTER_NAME="sumo-k8-cluster"

if [ "$K8S_TOOL" == "kind" ]; then
    if kind get clusters | grep -q "^${CLUSTER_NAME}$"; then
        echo "Cluster ${CLUSTER_NAME} already exists. Using existing cluster."
        kind get kubeconfig --name ${CLUSTER_NAME} > /tmp/sumo-k8-kubeconfig
    else
        echo "Creating kind cluster: ${CLUSTER_NAME}"
        kind create cluster --name ${CLUSTER_NAME} --wait 5m
        kind get kubeconfig --name ${CLUSTER_NAME} > /tmp/sumo-k8-kubeconfig
        echo "Cluster created successfully!"
    fi
    
    export KUBECONFIG=/tmp/sumo-k8-kubeconfig
    
elif [ "$K8S_TOOL" == "minikube" ]; then
    if minikube status -p ${CLUSTER_NAME} &> /dev/null; then
        echo "Minikube cluster ${CLUSTER_NAME} already running."
    else
        echo "Starting minikube cluster: ${CLUSTER_NAME}"
        minikube start -p ${CLUSTER_NAME} --wait=all
        echo "Cluster started successfully!"
    fi
    
    minikube kubectl --profile ${CLUSTER_NAME} -- get nodes
    eval $(minikube docker-env -p ${CLUSTER_NAME})
fi

echo ""
echo "Verifying cluster connection..."
kubectl cluster-info
kubectl get nodes

echo ""
echo "Kubernetes cluster is ready!"
echo ""
echo "To use this cluster:"
if [ "$K8S_TOOL" == "kind" ]; then
    echo "  export KUBECONFIG=/tmp/sumo-k8-kubeconfig"
elif [ "$K8S_TOOL" == "minikube" ]; then
    echo "  minikube kubectl --profile ${CLUSTER_NAME} -- <command>"
    echo "  Or: eval \$(minikube kubectl --profile ${CLUSTER_NAME} -- env)"
fi
echo ""
echo "You can now run the SUMO-K8 controller and it will detect Kubernetes."

