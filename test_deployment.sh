#!/bin/bash
set -e

echo "=== Testing SUMO-K8 on Minikube ==="
echo ""

# Check minikube
if ! command -v minikube &> /dev/null; then
    echo "Error: minikube not found. Installing..."
    echo "Please install minikube: https://minikube.sigs.k8s.io/docs/start/"
    exit 1
fi

# Start minikube if not running
if ! minikube status &> /dev/null; then
    echo "Starting minikube cluster..."
    minikube start --memory=4096 --cpus=2
else
    echo "Minikube cluster already running"
fi

# Set docker env for minikube
echo "Configuring Docker for minikube..."
eval $(minikube docker-env)

# Build Docker image
echo "Building Docker image..."
docker build -t sumo-k8-controller:latest .

# Deploy
echo "Deploying SUMO-K8..."
./deploy/deploy.sh --postgres --image sumo-k8-controller --tag latest

# Wait for deployment
echo "Waiting for deployment to be ready..."
kubectl wait --for=condition=available --timeout=300s deployment/sumo-k8-controller -n sumo-k8

# Port forward in background
echo "Setting up port forwarding..."
kubectl port-forward -n sumo-k8 svc/sumo-k8-controller 8000:80 &
PF_PID=$!
sleep 5

# Test health
echo "Testing health endpoint..."
curl -s http://localhost:8000/health | python3 -m json.tool || echo "Health check failed"

echo ""
echo "=== Deployment complete! ==="
echo "API available at: http://localhost:8000"
echo "Port forward PID: $PF_PID"
echo ""
echo "To stop port forwarding: kill $PF_PID"
