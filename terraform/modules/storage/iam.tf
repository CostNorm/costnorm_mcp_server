# IAM Role and Policy definitions specific to Storage Optimizer Lambdas (e.g., EBS)

resource "aws_iam_role" "lambda_role" {
  # 변수 사용 방식 변경: 모듈 내 변수 또는 고정값 사용 고려
  # name = "${var.function_name}-lambda-role" # 루트 var.function_name 대신 다른 방식 필요
  name = "storage-lambda-role" # 예시: Storage 모듈 공통 역할 이름

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# EBS Optimizer Lambda에 필요한 정책
resource "aws_iam_policy" "ebs_optimizer_policy" {
  # name = "${var.function_name}-policy" # 루트 var.function_name 대신 다른 방식 필요
  name        = "ebs-optimizer-lambda-policy" # 예시: 고정된 정책 이름
  description = "IAM policy for EBS Optimizer Lambda function"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EC2DescribePermissions"
        Effect = "Allow"
        Action = [
          "ec2:DescribeVolumes",
          "ec2:DescribeInstances",
          "ec2:DescribeSnapshots",
          "ec2:DescribeRegions",
          "ec2:DescribeVolumesModifications"
        ]
        Resource = "*"
      },
      {
        Sid    = "EC2ActionPermissions"
        Effect = "Allow"
        Action = [
          "ec2:CreateSnapshot",
          "ec2:DeleteVolume",
          "ec2:ModifyVolume",
          "ec2:DetachVolume",
          "ec2:AttachVolume"
        ]
        Resource = "*"
      },
      {
        Sid    = "EC2TagPermissions"
        Effect = "Allow"
        Action = [
           "ec2:CreateTags"
        ]
        # var.region 대신 data.aws_region 사용 또는 외부 변수 전달 필요
        # Resource = "arn:aws:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:snapshot/*"
        Resource = "arn:aws:ec2:*:*:snapshot/*" # 좀 더 범용적인 형태 (리전/계정 ID 하드코딩 제거)
      },
      {
        Sid    = "CloudWatchMetricsPermissions"
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricStatistics"
        ]
        Resource = "*"
      },
    ]
  })
}

# EBS Optimizer 정책을 역할에 연결
resource "aws_iam_role_policy_attachment" "ebs_optimizer_policy_attachment" {
  role       = aws_iam_role.lambda_role.name # 위에서 정의한 역할 이름 사용
  policy_arn = aws_iam_policy.ebs_optimizer_policy.arn
}

# 계정 ID는 여전히 필요할 수 있음 (다른 리소스 ARN 구성 등)
data "aws_caller_identity" "current" {}

# 현재 리전 정보 가져오기 (필요 시)
# data "aws_region" "current" {}
