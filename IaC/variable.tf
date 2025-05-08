variable "awscli_profile" {
  type = string
  default = "costnorm"
}

variable "region" {
  type = string
  default = "ap-northeast-2"
}

variable "container_registry" {
  type = string
  default = "354918406440.dkr.ecr.ap-northeast-2.amazonaws.com"
}

variable "container_repository" {
  type = string
  default = "costnorm-mcp-server"
}

variable "container_tag" {
  type = string
  default = "latest"
}
