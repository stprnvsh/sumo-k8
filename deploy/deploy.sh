#!/bin/bash
# Deploy SUMO-K8 Controller to Kubernetes cluster

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
NAMESPACE="sumo-k8"
IMAGE_NAME="sumo-k8-controller"
IMAGE_TAG="latest"
DEPLOY_POSTGRES=false
DEPLOY_INGRESS=false
KUBECONFIG=""

usage() {
    cat << EOF
Deploy SUMO-K8 Controller to Kubernetes

Usage: $0 [OPTIONS]

Options:
    -n, --namespace NAME       Kubernetes namespace (default: sumo-k8)
    -i, --image IMAGE          Docker image name (default: sumo-k8-controller)
    -t, --tag TAG              Docker image tag (default: latest)
    -p, --postgres             Deploy PostgreSQL in cluster
    -g, --ingress              Deploy Ingress
    -k, --kubeconfig PATH      Path to kubeconfig file
    -d, --database-url URL     Database URL (required if not using --postgres)
    -h, --help                 Show this help message

Examples:
    # Deploy to existing cluster with external database
    $0 -d "postgresql://user:pass@host:5432/sumo_k8"

    # Deploy with PostgreSQL in cluster
    $0 --postgres

    # Deploy with custom image
    $0 -i myregistry/sumo-k8 -t v1.0.0 --postgres
EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -n|--namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        -i|--image)
            IMAGE_NAME="$2"
            shift 2
            ;;
        -t|--tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        -p|--postgres)
            DEPLOY_POSTGRES=true
            shift
            ;;
        -g|--ingress)
            DEPLOY_INGRESS=true
            shift
            ;;
        -k|--kubeconfig)
            KUBECONFIG="$2"
            export KUBECONFIG
            shift 2
            ;;
        -d|--database-url)
            DATABASE_URL="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            usage
            exit 1
            ;;
    esac
done

# Check kubectl
if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}Error: kubectl not found. Please install kubectl.${NC}"
    exit 1
fi

# Check cluster connection
echo -e "${YELLOW}Checking Kubernetes cluster connection...${NC}"
if ! kubectl cluster-info &> /dev/null; then
    echo -e "${RED}Error: Cannot connect to Kubernetes cluster${NC}"
    echo "Please ensure kubectl is configured correctly"
    exit 1
fi

echo -e "${GREEN}✓ Connected to cluster${NC}"
kubectl cluster-info | head -1

# Set database URL
if [ "$DEPLOY_POSTGRES" = true ]; then
    DATABASE_URL="postgresql://postgres:postgres@postgres-service:5432/sumo_k8"
    echo -e "${YELLOW}Using PostgreSQL in cluster${NC}"
elif [ -z "$DATABASE_URL" ]; then
    echo -e "${RED}Error: Database URL required. Use -d/--database-url or --postgres${NC}"
    usage
    exit 1
fi

# Create namespace
echo -e "${YELLOW}Creating namespace ${NAMESPACE}...${NC}"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

# Create secrets
echo -e "${YELLOW}Creating secrets...${NC}"
kubectl create secret generic sumo-k8-secrets \
    --from-literal=DATABASE_URL="$DATABASE_URL" \
    --from-literal=POSTGRES_PASSWORD="postgres" \
    -n "$NAMESPACE" \
    --dry-run=client -o yaml | kubectl apply -f -

# Deploy manifests
echo -e "${YELLOW}Deploying manifests...${NC}"

# Update image in deployment
sed "s|image:.*|image: ${IMAGE_NAME}:${IMAGE_TAG}|g" "$PROJECT_DIR/k8s/deployment.yaml" | \
    kubectl apply -f -

kubectl apply -f "$PROJECT_DIR/k8s/namespace.yaml"
kubectl apply -f "$PROJECT_DIR/k8s/serviceaccount.yaml"
kubectl apply -f "$PROJECT_DIR/k8s/configmap.yaml"
kubectl apply -f "$PROJECT_DIR/k8s/service.yaml"

if [ "$DEPLOY_POSTGRES" = true ]; then
    echo -e "${YELLOW}Deploying PostgreSQL...${NC}"
    kubectl apply -f "$PROJECT_DIR/k8s/postgres.yaml"
    
    echo -e "${YELLOW}Waiting for PostgreSQL to be ready...${NC}"
    kubectl wait --for=condition=available --timeout=300s deployment/postgres -n "$NAMESPACE"
    
    echo -e "${YELLOW}Initializing database schema...${NC}"
    sleep 5  # Give PostgreSQL time to start
    kubectl exec -n "$NAMESPACE" deployment/postgres -- \
        psql -U postgres -d sumo_k8 -f /docker-entrypoint-initdb.d/schema.sql 2>/dev/null || \
    kubectl cp "$PROJECT_DIR/schema.sql" "$NAMESPACE/$(kubectl get pod -n $NAMESPACE -l app=postgres -o jsonpath='{.items[0].metadata.name}'):/tmp/schema.sql" && \
    kubectl exec -n "$NAMESPACE" deployment/postgres -- \
        psql -U postgres -d sumo_k8 -f /tmp/schema.sql
fi

if [ "$DEPLOY_INGRESS" = true ]; then
    echo -e "${YELLOW}Deploying Ingress...${NC}"
    kubectl apply -f "$PROJECT_DIR/k8s/ingress.yaml"
fi

# Wait for deployment
echo -e "${YELLOW}Waiting for controller to be ready...${NC}"
kubectl wait --for=condition=available --timeout=300s deployment/sumo-k8-controller -n "$NAMESPACE"

# Get service info
echo ""
echo -e "${GREEN}✓ Deployment complete!${NC}"
echo ""
echo "Service Information:"
kubectl get svc sumo-k8-controller -n "$NAMESPACE"

echo ""
echo "Access the API:"
echo "  kubectl port-forward -n $NAMESPACE svc/sumo-k8-controller 8000:80"
echo "  curl http://localhost:8000/health"
echo ""

if [ "$DEPLOY_INGRESS" = true ]; then
    INGRESS_HOST=$(kubectl get ingress -n "$NAMESPACE" sumo-k8-controller -o jsonpath='{.spec.rules[0].host}' 2>/dev/null || echo "N/A")
    echo "Ingress:"
    echo "  Host: $INGRESS_HOST"
    echo "  Update /etc/hosts if using local domain"
fi

