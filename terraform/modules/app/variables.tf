variable "cluster_endpoint" {
  type = string
}

variable "cluster_ca" {
  type = string
}

variable "cluster_name" {
  type = string
}

variable "image_repository" {
  type = string
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "database_url" {
  type    = string
  default = null
}

variable "admin_key" {
  type      = string
  sensitive = true
}

variable "s3_bucket" {
  type = string
}

variable "s3_region" {
  type = string
}

