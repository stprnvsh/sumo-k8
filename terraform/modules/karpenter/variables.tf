variable "cluster_name" {
  type = string
}

variable "cluster_endpoint" {
  type = string
}

variable "cluster_id" {
  type = string
}

variable "cluster_ca" {
  type = string
}

variable "oidc_arn" {
  type = string
}

variable "simulation_instance_types" {
  type    = list(string)
  default = ["c5.4xlarge", "c7i.4xlarge", "c7i.8xlarge", "c5.9xlarge"]
}

variable "simulation_max_cpu" {
  type    = string
  default = "200"
}

variable "simulation_max_memory" {
  type    = string
  default = "1600Gi"
}

variable "tags" {
  type    = map(string)
  default = {}
}

