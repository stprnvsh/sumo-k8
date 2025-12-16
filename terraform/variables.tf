variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "eu-central-2"
}

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
  default     = "sumo-k8-cluster"
}

variable "kubernetes_version" {
  description = "Kubernetes version"
  type        = string
  default     = "1.33"
}

# VPC Configuration
variable "vpc_id" {
  description = "Existing VPC ID (leave null to create new)"
  type        = string
  default     = null
}

variable "vpc_cidr" {
  description = "VPC CIDR block (required if vpc_id is null)"
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_ids" {
  description = "Public subnet IDs (required if using existing VPC)"
  type        = list(string)
  default     = []
}

variable "private_subnet_ids" {
  description = "Private subnet IDs (required if using existing VPC)"
  type        = list(string)
  default     = []
}

# Infrastructure Nodes
variable "infrastructure_node_type" {
  description = "EC2 instance type for infrastructure nodes"
  type        = string
  default     = "t3.large"
}

variable "infrastructure_min_size" {
  description = "Minimum infrastructure nodes"
  type        = number
  default     = 1
}

variable "infrastructure_max_size" {
  description = "Maximum infrastructure nodes"
  type        = number
  default     = 3
}

# Simulation Nodes (Karpenter)
variable "simulation_instance_types" {
  description = "EC2 instance types for simulation nodes"
  type        = list(string)
  default     = ["c5.4xlarge", "c7i.4xlarge", "c7i.8xlarge", "c5.9xlarge"]
}

variable "simulation_max_cpu" {
  description = "Maximum total CPUs for simulation nodes"
  type        = string
  default     = "200"
}

variable "simulation_max_memory" {
  description = "Maximum total memory for simulation nodes"
  type        = string
  default     = "1600Gi"
}

# Application Configuration
variable "image_repository_name" {
  description = "ECR repository name"
  type        = string
  default     = "sumo-k8-controller"
}

variable "image_tag" {
  description = "Docker image tag"
  type        = string
  default     = "latest"
}

variable "database_url" {
  description = "PostgreSQL database URL (leave null to deploy in-cluster)"
  type        = string
  default     = null
}

variable "admin_key" {
  description = "Admin API key for protected endpoints"
  type        = string
  sensitive   = true
}

variable "s3_bucket_name" {
  description = "S3 bucket name for simulation results"
  type        = string
  default     = "transcality-simulations"
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}

