# SUMO-K8 Deployment Guide

Complete guide for deploying SUMO-K8 Controller to any Kubernetes cluster.

## Quick Start

### Option 1: Deploy to Existing Cluster

```bash
# With external database
./deploy/deploy.sh -d "postgresql://user:pass@host:5432/sumo_k8"

# With PostgreSQL in cluster
./deploy/deploy.sh --postgres
```

### Option 2: Create New Cluster + Deploy

```bash
# Auto-detect and create cluster
./deploy/create-cluster.sh

# Specific cluster type
./deploy/create-cluster.sh -t kind
./deploy/create-cluster.sh -t gke
./deploy/create-cluster.sh -t eks
./deploy/create-cluster.sh -t aks
```

## Deployment Methods

### 1. Using Deployment Scripts (Recommended)

#### Deploy to Existing Cluster

```bash
# Basic deployment with external database
./deploy/deploy.sh \
  -d "postgresql://user:password@db-host:5432/sumo_k8"

# Deploy with PostgreSQL in cluster
./deploy/deploy.sh --postgres

# Deploy with Ingress
./deploy/deploy.sh --postgres --ingress

# Custom image and namespace
./deploy/deploy.sh \
  -i myregistry/sumo-k8 \
  -t v1.0.0 \
  -n production \
  -d "postgresql://..."
```

#### Create New Cluster

```bash
# Auto-detect (kind/minikube/GKE/EKS/AKS)
./deploy/create-cluster.sh

# Specific type
./deploy/create-cluster.sh -t kind
./deploy/create-cluster.sh -t minikube
./deploy/create-cluster.sh -t gke --project my-project
./deploy/create-cluster.sh -t eks --region us-east-1
./deploy/create-cluster.sh -t aks --resource-group my-rg
```

### 2. Using kubectl (Manual)

```bash
# 1. Create namespace
kubectl create namespace sumo-k8

# 2. Create secrets
kubectl create secret generic sumo-k8-secrets \
  --from-literal=DATABASE_URL="postgresql://user:pass@host:5432/sumo_k8" \
  -n sumo-k8

# 3. Deploy manifests
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/serviceaccount.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml

# 4. Optional: Deploy PostgreSQL
kubectl apply -f k8s/postgres.yaml

# 5. Optional: Deploy Ingress
kubectl apply -f k8s/ingress.yaml
```

### 3. Using Helm

```bash
# Install with default values
helm install sumo-k8 ./helm/sumo-k8

# Install with custom values
helm install sumo-k8 ./helm/sumo-k8 \
  --set database.url="postgresql://user:pass@host:5432/sumo_k8" \
  --set replicaCount=3 \
  --set postgres.enabled=false

# Install with values file
helm install sumo-k8 ./helm/sumo-k8 -f my-values.yaml

# Upgrade
helm upgrade sumo-k8 ./helm/sumo-k8

# Uninstall
helm uninstall sumo-k8
```

### 4. Using Kustomize

```bash
# Base deployment
kubectl apply -k k8s/

# With overlays (create k8s/overlays/production/)
kubectl apply -k k8s/overlays/production/
```

## Cloud Provider Specific

### Google Cloud Platform (GKE)

```bash
# Create cluster
gcloud container clusters create sumo-k8-cluster \
  --region us-central1 \
  --num-nodes 2 \
  --machine-type e2-medium

# Get credentials
gcloud container clusters get-credentials sumo-k8-cluster \
  --region us-central1

# Deploy
./deploy/deploy.sh --postgres
```

### Amazon EKS

```bash
# Create cluster
eksctl create cluster \
  --name sumo-k8-cluster \
  --region us-east-1 \
  --node-type t3.medium \
  --nodes 2

# Deploy
./deploy/deploy.sh --postgres
```

### Azure AKS

```bash
# Create resource group
az group create --name sumo-k8-rg --location eastus

# Create cluster
az aks create \
  --resource-group sumo-k8-rg \
  --name sumo-k8-cluster \
  --node-count 2 \
  --enable-cluster-autoscaler \
  --min-count 1 \
  --max-count 5

# Get credentials
az aks get-credentials \
  --resource-group sumo-k8-rg \
  --name sumo-k8-cluster

# Deploy
./deploy/deploy.sh --postgres
```

## Database Options

### Option 1: PostgreSQL in Cluster

```bash
./deploy/deploy.sh --postgres
```

**Pros:**
- Simple setup
- No external dependencies
- Good for development/testing

**Cons:**
- Not production-ready (single instance)
- Data persistence depends on PVC
- No high availability

### Option 2: External PostgreSQL

```bash
./deploy/deploy.sh \
  -d "postgresql://user:password@postgres-host:5432/sumo_k8"
```

**Pros:**
- Production-ready
- Managed services (Cloud SQL, RDS, etc.)
- High availability options
- Backups included

**Cons:**
- Requires external database setup
- Network configuration needed

### Option 3: Managed Database Services

#### Google Cloud SQL
```bash
# Create Cloud SQL instance
gcloud sql instances create sumo-k8-db \
  --database-version=POSTGRES_14 \
  --tier=db-f1-micro \
  --region=us-central1

# Get connection name
CONNECTION_NAME=$(gcloud sql instances describe sumo-k8-db \
  --format="value(connectionName)")

# Deploy with Cloud SQL Proxy
./deploy/deploy.sh \
  -d "postgresql://user:pass@localhost:5432/sumo_k8"
```

#### AWS RDS
```bash
# Create RDS instance via AWS Console or CLI
# Then deploy:
./deploy/deploy.sh \
  -d "postgresql://user:pass@rds-endpoint:5432/sumo_k8"
```

## Ingress Configuration

### NGINX Ingress

```bash
# Install NGINX Ingress Controller
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.8.1/deploy/static/provider/cloud/deploy.yaml

# Deploy with Ingress
./deploy/deploy.sh --postgres --ingress

# Update /etc/hosts
echo "127.0.0.1 sumo-k8.local" | sudo tee -a /etc/hosts
```

### Cloud Load Balancers

#### GKE (GCE Ingress)
```yaml
# Update k8s/ingress.yaml
annotations:
  kubernetes.io/ingress.class: gce
spec:
  rules:
  - host: sumo-k8.yourdomain.com
```

#### EKS (ALB)
```yaml
# Update k8s/ingress.yaml
annotations:
  kubernetes.io/ingress.class: alb
  alb.ingress.kubernetes.io/scheme: internet-facing
  alb.ingress.kubernetes.io/target-type: ip
```

## Verification

```bash
# Check pods
kubectl get pods -n sumo-k8

# Check services
kubectl get svc -n sumo-k8

# Check logs
kubectl logs -n sumo-k8 deployment/sumo-k8-controller

# Test API
kubectl port-forward -n sumo-k8 svc/sumo-k8-controller 8000:80
curl http://localhost:8000/health
```

## Troubleshooting

### Pods Not Starting

```bash
# Check pod status
kubectl describe pod -n sumo-k8 -l app=sumo-k8-controller

# Check logs
kubectl logs -n sumo-k8 -l app=sumo-k8-controller
```

### Database Connection Issues

```bash
# Verify secret
kubectl get secret sumo-k8-secrets -n sumo-k8 -o yaml

# Test database connection from pod
kubectl exec -n sumo-k8 deployment/sumo-k8-controller -- \
  python -c "import psycopg2; print('OK')"
```

### RBAC Issues

```bash
# Check service account
kubectl get sa -n sumo-k8

# Check cluster role binding
kubectl get clusterrolebinding sumo-k8-controller
```

## Production Checklist

- [ ] Use managed database (Cloud SQL, RDS, etc.)
- [ ] Enable TLS/HTTPS via Ingress
- [ ] Set resource limits appropriately
- [ ] Configure autoscaling
- [ ] Set up monitoring (Prometheus/Grafana)
- [ ] Configure backup strategy
- [ ] Use secrets management (Vault, etc.)
- [ ] Enable network policies
- [ ] Set up log aggregation
- [ ] Configure alerting

## Next Steps

After deployment:

1. **Create tenant account**:
   ```bash
   curl -X POST http://your-api/auth/register \
     -H "Content-Type: application/json" \
     -d '{"tenant_id": "city-a", "max_cpu": 10, ...}'
   ```

2. **Submit test job**:
   ```bash
   curl -X POST http://your-api/jobs \
     -H "Authorization: Bearer <api_key>" \
     -F "sumo_files=@test.zip"
   ```

3. **Monitor cluster**:
   ```bash
   kubectl get pods -n sumo-k8 -w
   ```

See [README.md](../README.md) for API usage examples.

