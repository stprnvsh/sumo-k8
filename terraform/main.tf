provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" {
  state = "available"
}

# VPC (use existing or create new)
module "vpc" {
  source = "./modules/vpc"
  
  vpc_id              = var.vpc_id
  vpc_cidr            = var.vpc_cidr
  public_subnet_ids   = var.public_subnet_ids
  private_subnet_ids  = var.private_subnet_ids
  create_vpc          = var.vpc_id == null
  availability_zones  = data.aws_availability_zones.available.names
  cluster_name        = var.cluster_name
  
  tags = var.tags
}

# EKS Cluster
module "eks" {
  source = "./modules/eks"
  
  cluster_name    = var.cluster_name
  cluster_version = var.kubernetes_version
  vpc_id          = module.vpc.vpc_id
  subnet_ids      = concat(module.vpc.public_subnet_ids, module.vpc.private_subnet_ids)
  
  infrastructure_node_type = var.infrastructure_node_type
  infrastructure_min_size = var.infrastructure_min_size
  infrastructure_max_size = var.infrastructure_max_size
  
  tags = var.tags
}

# EBS CSI Driver
module "ebs_csi" {
  source = "./modules/ebs-csi"
  
  cluster_name     = var.cluster_name
  cluster_id       = module.eks.cluster_id
  cluster_endpoint = module.eks.cluster_endpoint
  cluster_ca       = module.eks.cluster_certificate_authority_data
  oidc_arn         = module.eks.oidc_provider_arn
  
  tags = var.tags
  
  depends_on = [module.eks]
}

# Karpenter
module "karpenter" {
  source = "./modules/karpenter"
  
  cluster_name     = var.cluster_name
  cluster_endpoint = module.eks.cluster_endpoint
  cluster_ca       = module.eks.cluster_certificate_authority_data
  cluster_id       = module.eks.cluster_id
  oidc_arn         = module.eks.oidc_provider_arn
  
  simulation_instance_types = var.simulation_instance_types
  simulation_max_cpu        = var.simulation_max_cpu
  simulation_max_memory     = var.simulation_max_memory
  
  tags = var.tags
  
  depends_on = [module.eks, module.vpc]
}

# ECR Repository
module "ecr" {
  source = "./modules/ecr"
  
  repository_name = var.image_repository_name
}

# S3 Bucket for Results
module "s3" {
  source = "./modules/s3"
  
  bucket_name = var.s3_bucket_name
  region      = var.aws_region
}

# Application Deployment
module "app" {
  source = "./modules/app"
  
  cluster_endpoint = module.eks.cluster_endpoint
  cluster_ca       = module.eks.cluster_certificate_authority_data
  cluster_name     = var.cluster_name
  
  image_repository = module.ecr.repository_url
  image_tag        = var.image_tag
  
  database_url = var.database_url
  admin_key    = var.admin_key
  s3_bucket    = module.s3.bucket_name
  s3_region    = var.aws_region
  
  depends_on = [
    module.eks,
    module.ebs_csi,
    module.karpenter
  ]
}

