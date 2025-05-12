module "vpc" {
  source = "terraform-aws-modules/vpc/aws"

  name = "costnorm-mcp-vpc"
  cidr = "192.168.0.0/16"

  azs                     = ["${var.region}a", "${var.region}c"]
  public_subnets          = ["192.168.10.0/24", "192.168.20.0/24"]
  map_public_ip_on_launch = true

  enable_dns_hostnames = true
  enable_dns_support = true

  public_subnet_tags = {
    "CostNormExclude" = "true"
  }
}


# ecr endpoint
resource "aws_security_group" "ecr_endpoint_sg" {
  name = "costnorm-mcp-ecr-endpoint-sg"
  description = "Security group for ecr endpoint"
  vpc_id = module.vpc.vpc_id
  ingress {
    from_port = 443
    to_port = 443
    protocol = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port = 0
    to_port = 0
    protocol = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_vpc_endpoint" "ecr_api_endpoint" {
  vpc_id = module.vpc.vpc_id
  service_name = "com.amazonaws.${var.region}.ecr.api"
  vpc_endpoint_type = "Interface"
  security_group_ids = [aws_security_group.ecr_endpoint_sg.id]
  subnet_ids = module.vpc.public_subnets
  private_dns_enabled = true
}

resource "aws_vpc_endpoint" "ecr_dkr_endpoint" {
  vpc_id = module.vpc.vpc_id
  service_name = "com.amazonaws.${var.region}.ecr.dkr"
  vpc_endpoint_type = "Interface"
  security_group_ids = [aws_security_group.ecr_endpoint_sg.id]
  subnet_ids = module.vpc.public_subnets
  private_dns_enabled = true
}

