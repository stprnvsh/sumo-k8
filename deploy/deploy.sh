#!/bin/bash
# Deploy SUMO-K8 Controller to Kubernetes cluster

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
NAMESPACE="sumo-k8"
IMAGE_NAME="sumo-k8-controller"
IMAGE_TAG="latest"
DEPLOY_POSTGRES=true
DEPLOY_INGRESS=false
KUBECONFIG=""
AWS_REGION="eu-central-2"
AWS_ACCOUNT_ID=""

usage() {
    cat << EOF
Deploy SUMO-K8 Controller to Kubernetes

Usage: $0 [OPTIONS]

Options:
    -n, --namespace NAME       Kubernetes namespace (default: sumo-k8)
    -i, --image IMAGE          Docker image name (default: sumo-k8-controller)
    -t, --tag TAG              Docker image tag (default: latest)
    -p, --postgres BOOL        Deploy PostgreSQL in cluster (default: true)
    -g, --ingress              Deploy Ingress
    -k, --kubeconfig PATH      Path to kubeconfig file
    -d, --database-url URL     Database URL (required if not using --postgres)
    -r, --region REGION        AWS region (default: eu-central-2)
    -h, --help                 Show this help message

Examples:
    # Deploy with PostgreSQL (default)
    $0

    # Deploy to existing cluster with external database
    $0 -d "postgresql://user:pass@host:5432/sumo_k8" -p false

    # Deploy with custom image
    $0 -i myregistry/sumo-k8 -t v1.0.0
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
            DEPLOY_POSTGRES="$2"
            shift 2
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
        -r|--region)
            AWS_REGION="$2"
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

# Get AWS account ID
if command -v aws &> /dev/null; then
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
fi

# Set database URL
if [ "$DEPLOY_POSTGRES" = true ] || [ "$DEPLOY_POSTGRES" = "true" ]; then
    DATABASE_URL="postgresql://postgres:postgres@postgres-service:5432/sumo_k8"
    echo -e "${YELLOW}Using PostgreSQL in cluster${NC}"
elif [ -z "$DATABASE_URL" ]; then
    echo -e "${RED}Error: Database URL required. Use -d/--database-url or --postgres${NC}"
    usage
    exit 1
fi

# Determine full image name
if [[ "$IMAGE_NAME" != *"."* ]] && [ -n "$AWS_ACCOUNT_ID" ]; then
    # Local image name, use ECR
    FULL_IMAGE="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${IMAGE_NAME}:${IMAGE_TAG}"
    echo -e "${YELLOW}Using ECR image: ${FULL_IMAGE}${NC}"
else
    FULL_IMAGE="${IMAGE_NAME}:${IMAGE_TAG}"
fi

# ============================================================================
# STEP 1: Create namespace
# ============================================================================
echo -e "${BLUE}[1/8] Creating namespace ${NAMESPACE}...${NC}"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

# ============================================================================
# STEP 2: Apply RBAC (service account, roles, bindings)
# ============================================================================
echo -e "${BLUE}[2/8] Applying RBAC...${NC}"
kubectl apply -f "$PROJECT_DIR/k8s/serviceaccount.yaml"
kubectl apply -f "$PROJECT_DIR/k8s/rbac.yaml"

# ============================================================================
# STEP 3: Apply storage class (for EKS with EBS CSI driver)
# ============================================================================
echo -e "${BLUE}[3/8] Applying storage class...${NC}"
kubectl apply -f "$PROJECT_DIR/k8s/storageclass.yaml" 2>/dev/null || echo "Storage class already exists or not needed"

# Remove default from gp2 if it exists
kubectl annotate storageclass gp2 storageclass.kubernetes.io/is-default-class=false --overwrite 2>/dev/null || true

# ============================================================================
# STEP 4: Create secrets
# ============================================================================
echo -e "${BLUE}[4/8] Creating secrets...${NC}"
kubectl create secret generic sumo-k8-secrets \
    --from-literal=DATABASE_URL="$DATABASE_URL" \
    --from-literal=POSTGRES_PASSWORD="postgres" \
    -n "$NAMESPACE" \
    --dry-run=client -o yaml | kubectl apply -f -

# ============================================================================
# STEP 5: Apply configmap
# ============================================================================
echo -e "${BLUE}[5/8] Applying configmap...${NC}"
kubectl apply -f "$PROJECT_DIR/k8s/configmap.yaml"

# ============================================================================
# STEP 6: Deploy PostgreSQL (if enabled)
# ============================================================================
if [ "$DEPLOY_POSTGRES" = true ] || [ "$DEPLOY_POSTGRES" = "true" ]; then
    echo -e "${BLUE}[6/8] Deploying PostgreSQL...${NC}"
    kubectl apply -f "$PROJECT_DIR/k8s/postgres.yaml"
    
    echo -e "${YELLOW}Waiting for PostgreSQL pod to be scheduled...${NC}"
    # Wait for pod to exist
    for i in {1..30}; do
        POD_NAME=$(kubectl get pod -n "$NAMESPACE" -l app=postgres -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
        if [ -n "$POD_NAME" ]; then
            echo "Pod found: $POD_NAME"
            break
        fi
        echo "Waiting for postgres pod... ($i/30)"
        sleep 5
    done
    
    echo -e "${YELLOW}Waiting for PostgreSQL to be ready...${NC}"
    kubectl wait --for=condition=ready pod -l app=postgres -n "$NAMESPACE" --timeout=300s || {
        echo -e "${RED}PostgreSQL pod not ready. Checking status...${NC}"
        kubectl get pods -n "$NAMESPACE" -l app=postgres
        kubectl describe pod -n "$NAMESPACE" -l app=postgres | tail -20
    }
    
    # Initialize database schema
    echo -e "${YELLOW}Initializing database schema...${NC}"
    POD_NAME=$(kubectl get pod -n "$NAMESPACE" -l app=postgres -o jsonpath='{.items[0].metadata.name}')
    if [ -n "$POD_NAME" ]; then
        kubectl cp "$PROJECT_DIR/schema.sql" "$NAMESPACE/$POD_NAME:/tmp/schema.sql" 2>/dev/null || true
        kubectl exec -n "$NAMESPACE" "$POD_NAME" -- psql -U postgres -d sumo_k8 -f /tmp/schema.sql 2>/dev/null || echo "Schema may already exist"
    fi
else
    echo -e "${BLUE}[6/8] Skipping PostgreSQL (using external database)${NC}"
fi

# ============================================================================
# STEP 7: Deploy controller
# ============================================================================
echo -e "${BLUE}[7/8] Deploying SUMO-K8 controller...${NC}"

# Update image in deployment and apply
sed "s|image:.*sumo-k8-controller.*|image: ${FULL_IMAGE}|g" "$PROJECT_DIR/k8s/deployment.yaml" | kubectl apply -f -

kubectl apply -f "$PROJECT_DIR/k8s/service.yaml"

echo -e "${YELLOW}Waiting for controller to be ready...${NC}"
kubectl wait --for=condition=available --timeout=300s deployment/sumo-k8-controller -n "$NAMESPACE" || {
    echo -e "${RED}Controller not ready. Checking status...${NC}"
    kubectl get pods -n "$NAMESPACE" -l app=sumo-k8-controller
    kubectl describe pod -n "$NAMESPACE" -l app=sumo-k8-controller | tail -30
    kubectl logs -n "$NAMESPACE" -l app=sumo-k8-controller --tail=20 2>/dev/null || true
}

# ============================================================================
# STEP 8: Deploy Cluster Autoscaler (optional, for EKS)
# ============================================================================
echo -e "${BLUE}[8/9] Checking Cluster Autoscaler...${NC}"
if kubectl get deployment cluster-autoscaler -n kube-system &>/dev/null; then
    echo -e "${YELLOW}Cluster Autoscaler already exists, skipping${NC}"
else
    echo -e "${YELLOW}Note: Cluster Autoscaler requires IAM permissions. Install manually if needed:${NC}"
    echo "  kubectl apply -f $PROJECT_DIR/k8s/cluster-autoscaler.yaml"
    echo "  Then attach IAM policy to node instance roles"
fi

# ============================================================================
# STEP 9: Deploy Ingress (if enabled)
# ============================================================================
if [ "$DEPLOY_INGRESS" = true ]; then
    echo -e "${BLUE}[9/9] Deploying Ingress...${NC}"
    kubectl apply -f "$PROJECT_DIR/k8s/ingress.yaml"
else
    echo -e "${BLUE}[9/9] Skipping Ingress${NC}"
fi

# ============================================================================
# Summary
# ============================================================================
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}✓ Deployment complete!${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo "Namespace: $NAMESPACE"
echo "Image: $FULL_IMAGE"
echo ""
echo -e "${YELLOW}Pod Status:${NC}"
kubectl get pods -n "$NAMESPACE" -o wide
echo ""
echo -e "${YELLOW}Service Information:${NC}"
kubectl get svc -n "$NAMESPACE"
echo ""
echo -e "${YELLOW}PVC Status:${NC}"
kubectl get pvc -n "$NAMESPACE"
echo ""
echo -e "${GREEN}Access the API:${NC}"
echo "  kubectl port-forward -n $NAMESPACE svc/sumo-k8-controller 8000:80"
echo "  curl http://localhost:8000/health"
echo ""

if [ "$DEPLOY_INGRESS" = true ]; then
    INGRESS_HOST=$(kubectl get ingress -n "$NAMESPACE" sumo-k8-controller -o jsonpath='{.spec.rules[0].host}' 2>/dev/null || echo "N/A")
    echo "Ingress:"
    echo "  Host: $INGRESS_HOST"
    echo "  Update /etc/hosts if using local domain"
fi
