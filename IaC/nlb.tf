resource "aws_lb_target_group" "nlb_target_group" {
  name     = "costnorm-mcp-nlb-tg"
  port     = 8080
  protocol = "TCP"
  target_type = "ip"
  vpc_id = module.vpc.vpc_id

  depends_on = [ module.vpc ]
}

resource "aws_lb" "nlb" {
  name               = "costnorm-mcp-server-nlb"
  load_balancer_type = "network"
  subnets            = module.vpc.public_subnets

  depends_on = [ module.vpc ]
}

resource "aws_lb_listener" "nlb_listener" {
  load_balancer_arn = aws_lb.nlb.arn
  port              = 8080
  protocol          = "TCP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.nlb_target_group.arn
  }

  depends_on = [ module.vpc ]
}

