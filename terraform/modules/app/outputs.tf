output "load_balancer_url" {
  value       = try(kubernetes_service.controller.status[0].load_balancer[0].ingress[0].hostname, "")
  description = "Load balancer hostname (may be empty until service is ready)"
}

