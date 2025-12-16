variable "cluster_name" {
  type = string
}

variable "cluster_id" {
  type = string
}

variable "oidc_arn" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "cluster_ca" {
  type = string
}

variable "cluster_endpoint" {
  type = string
}

