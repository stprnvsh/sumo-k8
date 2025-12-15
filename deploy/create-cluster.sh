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

# EKS-specific options
AWS_REGION=""
AWS_VPC_ID=""
AWS_VPC_CIDR=""
AWS_PUBLIC_SUBNETS=""
AWS_PRIVATE_SUBNETS=""
AWS_NODE_TYPE="c5.4xlarge"
AWS_NODES_MIN=0
AWS_NODES_MAX=100

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

EKS-specific options (when --type eks):
    --aws-region REGION       AWS region (default: eu-central-2)
    --aws-vpc-id VPC_ID       Existing VPC ID to use
    --aws-vpc-cidr CIDR       VPC CIDR block (required if --aws-vpc-id specified)
    --aws-public-subnets      Comma-separated public subnet IDs
    --aws-private-subnets     Comma-separated private subnet IDs
    --aws-node-type TYPE      EC2 instance type (default: c5.4xlarge)
    --aws-nodes-min N         Minimum nodes (default: 0)
    --aws-nodes-max N         Maximum nodes (default: 100)

Examples:
    # Auto-detect and create cluster
    $0

    # Create kind cluster
    $0 -t kind

    # Create EKS cluster with default settings
    $0 -t eks

    # Create EKS cluster with existing VPC
    $0 -t eks --aws-region eu-central-2 \\
        --aws-vpc-id vpc-xxx \\
        --aws-vpc-cidr 172.31.0.0/16 \\
        --aws-public-subnets subnet-xxx,subnet-yyy \\
        --aws-private-subnets subnet-xxx,subnet-yyy

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
    
    # Set defaults
    AWS_REGION="${AWS_REGION:-eu-central-2}"
    
    echo "Region: $AWS_REGION"
    echo "Node type: $AWS_NODE_TYPE"
    echo "Nodes: min=$AWS_NODES_MIN, max=$AWS_NODES_MAX"
    
    # Check if eksctl is installed
    if ! command -v eksctl &> /dev/null; then
        echo -e "${YELLOW}eksctl not found. Installing...${NC}"
        if [[ "$OSTYPE" == "darwin"* ]]; then
            brew install eksctl || {
                echo -e "${RED}Failed to install eksctl via brew. Please install manually:${NC}"
                echo "  brew install eksctl"
                echo "  Or: https://github.com/weaveworks/eksctl/releases"
                exit 1
            }
        else
            echo -e "${RED}Please install eksctl:${NC}"
            echo "  https://github.com/weaveworks/eksctl/releases"
            exit 1
        fi
    fi
    
    # Build eksctl command - create cluster without node groups first
    # Using Kubernetes 1.33 for latest features and EBS CSI driver compatibility
    EKSCTL_CMD="eksctl create cluster \
        --name=\"${CLUSTER_NAME}\" \
        --region=\"${AWS_REGION}\" \
        --version=1.33 \
        --without-nodegroup \
        --with-oidc \
        --asg-access \
        --full-ecr-access \
        --alb-ingress-access"
    
    # Add VPC/subnet options if provided
    if [ -n "$AWS_VPC_ID" ]; then
        if [ -z "$AWS_VPC_CIDR" ]; then
            echo -e "${RED}Error: --aws-vpc-cidr required when --aws-vpc-id is specified${NC}"
            exit 1
        fi
        if [ -z "$AWS_PUBLIC_SUBNETS" ] || [ -z "$AWS_PRIVATE_SUBNETS" ]; then
            echo -e "${RED}Error: --aws-public-subnets and --aws-private-subnets required when --aws-vpc-id is specified${NC}"
            exit 1
        fi
        echo -e "${YELLOW}Using existing VPC: ${AWS_VPC_ID}${NC}"
        echo -e "${YELLOW}VPC CIDR: ${AWS_VPC_CIDR}${NC}"
        echo -e "${YELLOW}Public subnets: ${AWS_PUBLIC_SUBNETS}${NC}"
        echo -e "${YELLOW}Private subnets: ${AWS_PRIVATE_SUBNETS}${NC}"
        EKSCTL_CMD="$EKSCTL_CMD \
            --vpc-public-subnets=\"${AWS_PUBLIC_SUBNETS}\" \
            --vpc-private-subnets=\"${AWS_PRIVATE_SUBNETS}\""
    else
        echo -e "${YELLOW}Creating new VPC (eksctl will manage)${NC}"
    fi
    
    # Execute eksctl command to create cluster
    eval $EKSCTL_CMD
    
    echo -e "${GREEN}✓ EKS cluster created${NC}"
    
    # ============================================================================
    # NODE GROUP SETUP: Infrastructure vs Simulation Nodes
    # ============================================================================
    # INFRASTRUCTURE NODES (medium, always-on):
    #   - Instance type: t3.large (2 vCPU, 8GB RAM, ~$0.0832/hour)
    #   - Purpose: Controller API, PostgreSQL, system components
    #   - Scaling: 1-3 nodes (always at least 1 running)
    #   - Cost: ~$60/month for 1 node (always on)
    #
    # SIMULATION NODES (large, on-demand):
    #   - Instance type: c5.4xlarge (16 vCPU, 32GB RAM, ~$0.68/hour)
    #   - Purpose: SUMO simulation jobs only
    #   - Scaling: 0-100 nodes (created on-demand, scale to 0 when idle)
    #   - Cost: Only pay when simulations are running
    # ============================================================================
    
    # Create infrastructure node group (medium instances for controller, postgres)
    echo -e "${YELLOW}Creating infrastructure node group (t3.large - always-on)...${NC}"
    eksctl create nodegroup \
        --cluster="${CLUSTER_NAME}" \
        --region="${AWS_REGION}" \
        --name=infrastructure-nodes \
        --node-type=t3.large \
        --nodes=1 \
        --nodes-min=1 \
        --nodes-max=3 \
        --managed \
        --tags="k8s.io/cluster-autoscaler/enabled=true,k8s.io/cluster-autoscaler/${CLUSTER_NAME}=owned"
    
    echo -e "${GREEN}✓ Infrastructure node group created${NC}"
    echo -e "${YELLOW}  Labeling infrastructure nodes...${NC}"
    
    # Wait for infrastructure node and label it
    sleep 10
    kubectl wait --for=condition=Ready nodes -l eks.amazonaws.com/nodegroup=infrastructure-nodes --timeout=300s 2>/dev/null || true
    kubectl label nodes -l eks.amazonaws.com/nodegroup=infrastructure-nodes node-type=infrastructure --overwrite 2>/dev/null || true
    
    # Create simulation node group (large instances, scale from 0)
    echo -e "${YELLOW}Creating simulation node group (${AWS_NODE_TYPE} - on-demand, scale from 0)...${NC}"
    eksctl create nodegroup \
        --cluster="${CLUSTER_NAME}" \
        --region="${AWS_REGION}" \
        --name=simulation-nodes \
        --node-type="${AWS_NODE_TYPE}" \
        --nodes=0 \
        --nodes-min="${AWS_NODES_MIN}" \
        --nodes-max="${AWS_NODES_MAX}" \
        --managed \
        --tags="k8s.io/cluster-autoscaler/enabled=true,k8s.io/cluster-autoscaler/${CLUSTER_NAME}=owned"
    
    echo -e "${GREEN}✓ Simulation node group created${NC}"
    echo -e "${YELLOW}  Note: Simulation nodes will be auto-labeled when created${NC}"
    
    # Create a DaemonSet to auto-label simulation nodes when they join
    cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: node-labeler
  namespace: kube-system
spec:
  selector:
    matchLabels:
      name: node-labeler
  template:
    metadata:
      labels:
        name: node-labeler
    spec:
      hostNetwork: true
      containers:
      - name: labeler
        image: bitnami/kubectl:latest
        command:
        - /bin/sh
        - -c
        - |
          while true; do
            kubectl label nodes -l eks.amazonaws.com/nodegroup=simulation-nodes node-type=simulation --overwrite 2>/dev/null || true
            sleep 1
          done
      tolerations:
      - operator: Exists
EOF
    echo -e "${GREEN}✓ Node labeler DaemonSet created (auto-labels simulation nodes)${NC}"
    
    # ============================================================================
    # INSTALL EBS CSI DRIVER ADDON (Required for Kubernetes 1.33+ storage)
    # ============================================================================
    echo -e "${YELLOW}Installing EBS CSI Driver addon...${NC}"
    
    # Create IAM role for EBS CSI driver
    eksctl create iamserviceaccount \
        --name ebs-csi-controller-sa \
        --namespace kube-system \
        --cluster "${CLUSTER_NAME}" \
        --region "${AWS_REGION}" \
        --role-name "AmazonEKS_EBS_CSI_DriverRole_${CLUSTER_NAME}" \
        --role-only \
        --attach-policy-arn arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy \
        --approve
    
    # Get the role ARN
    EBS_CSI_ROLE_ARN=$(aws iam get-role --role-name "AmazonEKS_EBS_CSI_DriverRole_${CLUSTER_NAME}" --query 'Role.Arn' --output text)
    
    # Install EBS CSI driver addon
    eksctl create addon \
        --name aws-ebs-csi-driver \
        --cluster "${CLUSTER_NAME}" \
        --region "${AWS_REGION}" \
        --service-account-role-arn "${EBS_CSI_ROLE_ARN}" \
        --force
    
    echo -e "${GREEN}✓ EBS CSI Driver addon installed${NC}"
    
    # Create gp3 storage class (default for EKS)
    echo -e "${YELLOW}Creating gp3 storage class...${NC}"
    cat <<SCEOF | kubectl apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: ebs-gp3
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: ebs.csi.aws.com
parameters:
  type: gp3
  fsType: ext4
  encrypted: "true"
reclaimPolicy: Delete
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: true
SCEOF
    
    # Remove default annotation from gp2 if it exists
    kubectl annotate storageclass gp2 storageclass.kubernetes.io/is-default-class=false --overwrite 2>/dev/null || true
    
    echo -e "${GREEN}✓ Storage class configured${NC}"
    
    # ============================================================================
    # INSTALL CLUSTER AUTOSCALER (Required for simulation node auto-scaling)
    # ============================================================================
    echo -e "${YELLOW}Installing Cluster Autoscaler...${NC}"
    
    # Create IAM policy for Cluster Autoscaler
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
    
    cat > /tmp/cluster-autoscaler-policy.json << 'POLICYEOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "autoscaling:DescribeAutoScalingGroups",
        "autoscaling:DescribeAutoScalingInstances",
        "autoscaling:DescribeLaunchConfigurations",
        "autoscaling:DescribeScalingActivities",
        "autoscaling:DescribeTags",
        "ec2:DescribeInstanceTypes",
        "ec2:DescribeLaunchTemplateVersions"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "autoscaling:SetDesiredCapacity",
        "autoscaling:TerminateInstanceInAutoScalingGroup",
        "ec2:DescribeImages",
        "ec2:GetInstanceTypesFromInstanceRequirements",
        "eks:DescribeNodegroup"
      ],
      "Resource": "*"
    }
  ]
}
POLICYEOF
    
    # Create policy if it doesn't exist
    POLICY_ARN=$(aws iam list-policies --query "Policies[?PolicyName=='EKSClusterAutoscalerPolicy'].Arn" --output text --region "${AWS_REGION}" 2>/dev/null || echo "")
    if [ -z "$POLICY_ARN" ]; then
        POLICY_ARN=$(aws iam create-policy --policy-name EKSClusterAutoscalerPolicy --policy-document file:///tmp/cluster-autoscaler-policy.json --region "${AWS_REGION}" --query 'Policy.Arn' --output text 2>/dev/null || echo "")
    fi
    
    if [ -n "$POLICY_ARN" ]; then
        # Attach policy to infrastructure node role
        INFRA_ROLE=$(aws eks describe-nodegroup --cluster-name "${CLUSTER_NAME}" --nodegroup-name infrastructure-nodes --region "${AWS_REGION}" --query 'nodegroup.nodeRole' --output text 2>/dev/null | cut -d'/' -f2)
        if [ -n "$INFRA_ROLE" ]; then
            aws iam attach-role-policy --role-name "$INFRA_ROLE" --policy-arn "$POLICY_ARN" 2>/dev/null || true
        fi
        
        # Attach policy to simulation node role
        SIM_ROLE=$(aws eks describe-nodegroup --cluster-name "${CLUSTER_NAME}" --nodegroup-name simulation-nodes --region "${AWS_REGION}" --query 'nodegroup.nodeRole' --output text 2>/dev/null | cut -d'/' -f2)
        if [ -n "$SIM_ROLE" ]; then
            aws iam attach-role-policy --role-name "$SIM_ROLE" --policy-arn "$POLICY_ARN" 2>/dev/null || true
        fi
        
        # Deploy Cluster Autoscaler
        kubectl apply -f "$PROJECT_DIR/k8s/cluster-autoscaler.yaml"
        
        echo -e "${GREEN}✓ Cluster Autoscaler installed (scales simulation nodes 0-50, 30s scale-down)${NC}"
    else
        echo -e "${YELLOW}⚠ Could not create IAM policy. Install Cluster Autoscaler manually:${NC}"
        echo "  kubectl apply -f $PROJECT_DIR/k8s/cluster-autoscaler.yaml"
    fi
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
        --aws-region)
            AWS_REGION="$2"
            shift 2
            ;;
        --aws-vpc-id)
            AWS_VPC_ID="$2"
            shift 2
            ;;
        --aws-vpc-cidr)
            AWS_VPC_CIDR="$2"
            shift 2
            ;;
        --aws-public-subnets)
            AWS_PUBLIC_SUBNETS="$2"
            shift 2
            ;;
        --aws-private-subnets)
            AWS_PRIVATE_SUBNETS="$2"
            shift 2
            ;;
        --aws-node-type)
            AWS_NODE_TYPE="$2"
            shift 2
            ;;
        --aws-nodes-min)
            AWS_NODES_MIN="$2"
            shift 2
            ;;
        --aws-nodes-max)
            AWS_NODES_MAX="$2"
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

