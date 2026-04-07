#!/bin/bash
# Create a new Kubernetes cluster and deploy SUMO-K8

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

CLUSTER_NAME="sumo-k8-cluster"
CLUSTER_TYPE=""
DEPLOY_POSTGRES=true
DEPLOY_INGRESS=false

usage() {
    cat << EOF
Create a new Kubernetes cluster and deploy SUMO-K8 Controller

Usage: $0 [OPTIONS]

Options:
    -t, --type TYPE           Cluster type: kind, minikube, gke, eks, aks (default: auto-detect)
    -n, --name NAME           Cluster name (default: sumo-k8-cluster)
    -p, --no-postgres         Don't deploy PostgreSQL (use external DB)
    -g, --ingress             Deploy Ingress controller
    -h, --help                Show this help message

Examples:
    # Auto-detect and create cluster
    $0

    # Create kind cluster
    $0 -t kind

    # Create GKE cluster
    $0 -t gke --project my-project --region us-central1
EOF
}

# Detect available cluster tool
detect_cluster_tool() {
    if command -v kind &> /dev/null; then
        echo "kind"
    elif command -v minikube &> /dev/null; then
        echo "minikube"
    elif command -v gcloud &> /dev/null && gcloud container clusters list &> /dev/null; then
        echo "gke"
    elif command -v aws &> /dev/null && aws eks list-clusters &> /dev/null; then
        echo "eks"
    elif command -v az &> /dev/null && az aks list &> /dev/null; then
        echo "aks"
    else
        echo "none"
    fi
}

# Create kind cluster
create_kind_cluster() {
    echo -e "${BLUE}Creating kind cluster: ${CLUSTER_NAME}${NC}"
    
    if kind get clusters | grep -q "^${CLUSTER_NAME}$"; then
        echo -e "${YELLOW}Cluster ${CLUSTER_NAME} already exists${NC}"
        kind get kubeconfig --name "${CLUSTER_NAME}" > /tmp/sumo-k8-kubeconfig
    else
        kind create cluster --name "${CLUSTER_NAME}" --wait 5m
        kind get kubeconfig --name "${CLUSTER_NAME}" > /tmp/sumo-k8-kubeconfig
        echo -e "${GREEN}✓ Kind cluster created${NC}"
    fi
    
    export KUBECONFIG=/tmp/sumo-k8-kubeconfig
}

# Create minikube cluster
create_minikube_cluster() {
    echo -e "${BLUE}Creating minikube cluster: ${CLUSTER_NAME}${NC}"
    
    if minikube status -p "${CLUSTER_NAME}" &> /dev/null; then
        echo -e "${YELLOW}Cluster ${CLUSTER_NAME} already exists${NC}"
    else
        minikube start -p "${CLUSTER_NAME}" --wait=all
        echo -e "${GREEN}✓ Minikube cluster created${NC}"
    fi
}

# Create GKE cluster
create_gke_cluster() {
    echo -e "${BLUE}Creating GKE cluster: ${CLUSTER_NAME}${NC}"
    
    GCP_PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
    GCP_REGION="${GCP_REGION:-us-central1}"
    GCP_ZONE="${GCP_ZONE:-us-central1-a}"
    
    if [ -z "$GCP_PROJECT" ]; then
        echo -e "${RED}Error: GCP_PROJECT not set${NC}"
        exit 1
    fi
    
    echo "Project: $GCP_PROJECT"
    echo "Region: $GCP_REGION"
    
    gcloud container clusters create "${CLUSTER_NAME}" \
        --project="$GCP_PROJECT" \
        --region="$GCP_REGION" \
        --num-nodes=2 \
        --machine-type=e2-medium \
        --enable-autoscaling \
        --min-nodes=1 \
        --max-nodes=5 \
        --wait
    
    gcloud container clusters get-credentials "${CLUSTER_NAME}" \
        --project="$GCP_PROJECT" \
        --region="$GCP_REGION"
    
    echo -e "${GREEN}✓ GKE cluster created${NC}"
}

# Create EKS cluster
create_eks_cluster() {
    echo -e "${BLUE}Creating EKS cluster: ${CLUSTER_NAME}${NC}"
    
    AWS_REGION="${AWS_REGION:-us-east-1}"
    
    echo "Region: $AWS_REGION"
    
    eksctl create cluster \
        --name="${CLUSTER_NAME}" \
        --region="$AWS_REGION" \
        --node-type=t3.medium \
        --nodes=2 \
        --nodes-min=1 \
        --nodes-max=5 \
        --managed
    
    echo -e "${GREEN}✓ EKS cluster created${NC}"
}

# Create AKS cluster
create_aks_cluster() {
    echo -e "${BLUE}Creating AKS cluster: ${CLUSTER_NAME}${NC}"
    
    AZURE_RG="${AZURE_RG:-sumo-k8-rg}"
    AZURE_LOCATION="${AZURE_LOCATION:-eastus}"
    
    echo "Resource Group: $AZURE_RG"
    echo "Location: $AZURE_LOCATION"
    
    az group create --name "$AZURE_RG" --location "$AZURE_LOCATION"
    
    az aks create \
        --resource-group "$AZURE_RG" \
        --name "$CLUSTER_NAME" \
        --node-count 2 \
        --node-vm-size Standard_B2s \
        --enable-cluster-autoscaler \
        --min-count 1 \
        --max-count 5 \
        --generate-ssh-keys
    
    az aks get-credentials --resource-group "$AZURE_RG" --name "$CLUSTER_NAME"
    
    echo -e "${GREEN}✓ AKS cluster created${NC}"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -t|--type)
            CLUSTER_TYPE="$2"
            shift 2
            ;;
        -n|--name)
            CLUSTER_NAME="$2"
            shift 2
            ;;
        -p|--no-postgres)
            DEPLOY_POSTGRES=false
            shift
            ;;
        -g|--ingress)
            DEPLOY_INGRESS=true
            shift
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

# Auto-detect cluster type if not specified
if [ -z "$CLUSTER_TYPE" ]; then
    CLUSTER_TYPE=$(detect_cluster_tool)
    if [ "$CLUSTER_TYPE" = "none" ]; then
        echo -e "${RED}Error: No Kubernetes cluster tool found${NC}"
        echo "Please install one of: kind, minikube, gcloud, eksctl, or az"
        exit 1
    fi
    echo -e "${YELLOW}Auto-detected cluster type: ${CLUSTER_TYPE}${NC}"
fi

# Create cluster
case "$CLUSTER_TYPE" in
    kind)
        create_kind_cluster
        ;;
    minikube)
        create_minikube_cluster
        ;;
    gke)
        create_gke_cluster
        ;;
    eks)
        create_eks_cluster
        ;;
    aks)
        create_aks_cluster
        ;;
    *)
        echo -e "${RED}Error: Unknown cluster type: ${CLUSTER_TYPE}${NC}"
        exit 1
        ;;
esac

# Build and load image (for local clusters)
if [ "$CLUSTER_TYPE" = "kind" ]; then
    echo -e "${YELLOW}Building Docker image...${NC}"
    docker build -t "${IMAGE_NAME:-sumo-k8-controller}:latest" "$PROJECT_DIR"
    
    echo -e "${YELLOW}Loading image into kind cluster...${NC}"
    kind load docker-image "${IMAGE_NAME:-sumo-k8-controller}:latest" --name "$CLUSTER_NAME"
elif [ "$CLUSTER_TYPE" = "minikube" ]; then
    echo -e "${YELLOW}Building Docker image...${NC}"
    eval $(minikube docker-env -p "$CLUSTER_NAME")
    docker build -t "${IMAGE_NAME:-sumo-k8-controller}:latest" "$PROJECT_DIR"
fi

# Deploy SUMO-K8
echo -e "${YELLOW}Deploying SUMO-K8 Controller...${NC}"
"$SCRIPT_DIR/deploy.sh" \
    --postgres="$DEPLOY_POSTGRES" \
    --ingress="$DEPLOY_INGRESS" \
    --image="${IMAGE_NAME:-sumo-k8-controller}" \
    --tag="latest"

echo ""
echo -e "${GREEN}✓ Setup complete!${NC}"
echo ""
echo "Cluster: $CLUSTER_NAME"
echo "Type: $CLUSTER_TYPE"
echo ""
echo "Access the API:"
echo "  kubectl port-forward -n sumo-k8 svc/sumo-k8-controller 8000:80"
echo "  curl http://localhost:8000/health"

