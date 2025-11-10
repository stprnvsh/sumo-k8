# Deployment Scripts

## Scripts

- **deploy.sh** - Deploy to existing Kubernetes cluster
- **create-cluster.sh** - Create new cluster and deploy

## Usage

### deploy.sh

Deploy SUMO-K8 Controller to an existing Kubernetes cluster.

```bash
# With external database
./deploy/deploy.sh -d "postgresql://user:pass@host:5432/sumo_k8"

# With PostgreSQL in cluster
./deploy/deploy.sh --postgres

# Full options
./deploy/deploy.sh \
  -n sumo-k8 \
  -i myregistry/sumo-k8 \
  -t v1.0.0 \
  --postgres \
  --ingress
```

### create-cluster.sh

Create a new Kubernetes cluster and deploy SUMO-K8.

```bash
# Auto-detect cluster type
./deploy/create-cluster.sh

# Specific type
./deploy/create-cluster.sh -t kind
./deploy/create-cluster.sh -t minikube
./deploy/create-cluster.sh -t gke
./deploy/create-cluster.sh -t eks
./deploy/create-cluster.sh -t aks
```

## Requirements

- `kubectl` - Kubernetes CLI
- `docker` - For building images (local clusters)
- Cloud CLI tools (for cloud clusters):
  - `gcloud` - Google Cloud
  - `aws` / `eksctl` - Amazon AWS
  - `az` - Microsoft Azure

See [docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md) for detailed instructions.
