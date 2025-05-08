module "ecs" {
  source = "terraform-aws-modules/ecs/aws"

  cluster_name = "costnorm-mcp-server"

  cluster_configuration = {
    execute_command_configuration = {
      logging = "OVERRIDE"
      log_configuration = {
        cloud_watch_log_group_name = "/aws/ecs/costnorm-mcp-server"
      }
    }
  }

  fargate_capacity_providers = {
    FARGATE = {
      default_capacity_provider_strategy = {
        weight = 50
      }
    }
    FARGATE_SPOT = {
      default_capacity_provider_strategy = {
        weight = 50
      }
    }
  }

  services = {
    costnorm-mcp-server = {
      cpu    = 1024
      memory = 2048

      assign_public_ip = true
      # Container definition(s)
      container_definitions = {
        costnorm-mcp-server = {
          cpu       = 1024
          memory    = 2048
          essential = true
          image     = "${var.container_registry}/${var.container_repository}:${var.container_tag}"
          port_mappings = [
            {
              name          = "costnorm-mcp-server"
              containerPort = 8080
              protocol      = "tcp"
            }
          ]

          # Example image used requires access to write to root filesystem
          readonly_root_filesystem = false

          enable_cloudwatch_logging = true
          memory_reservation = 100
        }
      }

      load_balancer = {
        service = {
          target_group_arn = aws_lb_target_group.nlb_target_group.arn
          container_name   = "costnorm-mcp-server"
          container_port   = 8080
        }
      }

      subnet_ids = module.vpc.public_subnets
      security_group_rules = {
        alb_ingress_3000 = {
          type        = "ingress"
          from_port   = 8080
          to_port     = 8080
          protocol    = "tcp"
          description = "Service port"
          cidr_blocks = ["0.0.0.0/0"]
        }
        egress_all = {
          type        = "egress"
          from_port   = 0
          to_port     = 0
          protocol    = "-1"
          cidr_blocks = ["0.0.0.0/0"]
        }
      }
    }
  }

  depends_on = [ aws_lb_target_group.nlb_target_group, aws_lb.nlb, aws_lb_listener.nlb_listener, module.vpc ]
}
