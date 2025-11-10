# SUMO-K8 Setup Guide

Complete setup instructions for local development.

## Quick Start (Database Only)

```bash
./setup_local.sh
export DATABASE_URL="postgresql://$(whoami)@localhost/sumo_k8"
pip install -r requirements.txt
python app.py
```

## Full Setup (Database + Kubernetes)

```bash
./setup_local.sh --with-k8s
```

This will:
1. Set up PostgreSQL database
2. Create a local Kubernetes cluster (using kind or minikube)
3. Configure kubectl to use the cluster

## Manual Setup Steps

### 1. Database Setup

```bash
createdb sumo_k8
psql sumo_k8 < schema.sql
export DATABASE_URL="postgresql://$(whoami)@localhost/sumo_k8"
```

### 2. Kubernetes Setup (Optional)

#### Option A: Using kind (Recommended)

```bash
# Install kind
brew install kind  # macOS
# or: curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-linux-amd64

# Create cluster
./setup_k8s.sh

# Or manually:
kind create cluster --name sumo-k8-cluster
export KUBECONFIG=$(kind get kubeconfig --name sumo-k8-cluster)
```

#### Option B: Using minikube

```bash
# Install minikube
brew install minikube  # macOS

# Start cluster
minikube start --profile sumo-k8-cluster
```

#### Option C: Docker Desktop

Enable Kubernetes in Docker Desktop settings, then:

```bash
kubectl config use-context docker-desktop
```

### 3. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the Application

```bash
export DATABASE_URL="postgresql://$(whoami)@localhost/sumo_k8"
python app.py
```

The app will automatically detect if Kubernetes is available.

## Verification

### Check Database

```bash
psql sumo_k8 -c "SELECT * FROM tenants;"
```

### Check Kubernetes

```bash
kubectl get nodes
kubectl cluster-info
```

### Test API

```bash
# Check if app is running
curl http://localhost:8000/admin/activity

# Submit a job (requires K8s)
curl -X POST http://localhost:8000/jobs \
  -H "Authorization: Bearer key-city-a-12345" \
  -H "Content-Type: application/json" \
  -d '{"scenario_id": "test", "cpu_request": 2, "memory_gi": 4}'
```

## Troubleshooting

### PostgreSQL not running

```bash
# macOS with Homebrew
brew services start postgresql@14

# Or check status
brew services list | grep postgres
```

### Kubernetes not detected

1. Verify cluster is running:
   ```bash
   kubectl get nodes
   ```

2. Check kubectl context:
   ```bash
   kubectl config current-context
   ```

3. For kind, ensure KUBECONFIG is set:
   ```bash
   export KUBECONFIG=$(kind get kubeconfig --name sumo-k8-cluster)
   ```

### Port 8000 already in use

```bash
# Find and kill process
lsof -ti:8000 | xargs kill -9

# Or change port in app.py
uvicorn.run(app, host="0.0.0.0", port=8001)
```

## Cleanup

### Remove Kubernetes cluster

```bash
# kind
kind delete cluster --name sumo-k8-cluster

# minikube
minikube delete --profile sumo-k8-cluster
```

### Remove database

```bash
dropdb sumo_k8
```

