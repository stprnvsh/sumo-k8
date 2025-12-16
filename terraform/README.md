# SUMO-K8 Terraform Deployment

Terraform configuration to deploy SUMO-K8 on AWS EKS with Karpenter auto-scaling.

## Prerequisites

- Terraform >= 1.0
- AWS CLI configured
- kubectl installed
- Docker (for building images)

## Quick Start

1. **Copy example variables:**
   ```bash
   cp terraform.tfvars.example terraform.tfvars
   ```

2. **Edit `terraform.tfvars`** with your values:
   - AWS region
   - VPC/subnet IDs (or set `vpc_id = null` to create new)
   - Admin key
   - S3 bucket name

3. **Initialize Terraform:**
   ```bash
   cd terraform
   terraform init
   ```

4. **Plan deployment:**
   ```bash
   terraform plan
   ```

5. **Apply:**
   ```bash
   terraform apply
   ```

6. **Build and push Docker image:**
   ```bash
   # Get ECR URL from output
   ECR_URL=$(terraform output -raw ecr_repository_url)
   
   # Build and push
   docker build --platform linux/amd64 -t $ECR_URL:latest .
   aws ecr get-login-password --region eu-central-2 | docker login --username AWS --password-stdin $ECR_URL
   docker push $ECR_URL:latest
   ```

7. **Update kubeconfig:**
   ```bash
   terraform output -raw kubeconfig_command | bash
   ```

## Modules

- **vpc**: VPC, subnets, security groups
- **eks**: EKS cluster, infrastructure node group
- **ebs-csi**: EBS CSI driver addon
- **karpenter**: Karpenter installation and configuration
- **ecr**: ECR repository for Docker images
- **s3**: S3 bucket for simulation results
- **app**: Kubernetes application deployment

## Outputs

- `cluster_name`: EKS cluster name
- `kubeconfig_command`: Command to update kubeconfig
- `ecr_repository_url`: ECR repository URL
- `api_url`: Public API URL

## Destroy

```bash
terraform destroy
```

