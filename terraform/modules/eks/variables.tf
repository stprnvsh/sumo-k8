variable "cluster_name" {
  type = string
}

variable "cluster_version" {
  type    = string
  default = "1.33"
}

variable "subnet_ids" {
  type = list(string)
}

variable "infrastructure_node_type" {
  type    = string
  default = "t3.large"
}

variable "infrastructure_min_size" {
  type    = number
  default = 1
}

variable "infrastructure_max_size" {
  type    = number
  default = 3
}

variable "tags" {
  type    = map(string)
  default = {}
}

