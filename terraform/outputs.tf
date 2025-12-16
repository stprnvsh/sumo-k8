output "cluster_name" {
  description = "EKS cluster name"
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "EKS cluster endpoint"
  value       = module.eks.cluster_endpoint
}

output "kubeconfig_command" {
  description = "Command to update kubeconfig"
  value       = "aws eks update-kubeconfig --region ${var.aws_region} --name ${var.cluster_name}"
}

output "ecr_repository_url" {
  description = "ECR repository URL"
  value       = module.ecr.repository_url
}

output "s3_bucket_name" {
  description = "S3 bucket for results"
  value       = module.s3.bucket_name
}

output "load_balancer_url" {
  description = "Application load balancer URL"
  value       = module.app.load_balancer_url
}

output "api_url" {
  description = "Public API URL"
  value       = "http://${module.app.load_balancer_url}"
}

