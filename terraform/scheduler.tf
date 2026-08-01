data "archive_file" "scheduler_lambda" {
  type        = "zip"
  source_file = "${path.module}/../aws/ec2-scheduler-lambda/handler.py"
  output_path = "${path.module}/.terraform-build/scheduler-lambda.zip"
}

resource "aws_ssm_parameter" "current_ip" {
  name  = "/discord-bot/current-ip"
  type  = "String"
  value = "unset" # overwritten by the Lambda on every scheduled start

  lifecycle {
    ignore_changes = [value]
  }
}

# --- Lambda execution role: allowed to start/stop/describe this one
# instance and write the IP parameter above, nothing else. ---
resource "aws_iam_role" "lambda_exec" {
  name = "discord-bot-ec2-scheduler-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "lambda_exec" {
  name = "ec2-start-stop"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ec2:StartInstances", "ec2:StopInstances", "ec2:DescribeInstances"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:PutParameter"]
        Resource = aws_ssm_parameter.current_ip.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:*:*"
      },
    ]
  })
}

resource "aws_lambda_function" "ec2_scheduler" {
  function_name    = "discord-bot-ec2-scheduler"
  role              = aws_iam_role.lambda_exec.arn
  handler           = "handler.handler"
  runtime           = "python3.12"
  timeout           = 120
  filename          = data.archive_file.scheduler_lambda.output_path
  source_code_hash  = data.archive_file.scheduler_lambda.output_base64sha256

  environment {
    variables = {
      INSTANCE_ID       = aws_instance.k3s_node.id
      IP_PARAMETER_NAME = aws_ssm_parameter.current_ip.name
    }
  }
}

# --- EventBridge Scheduler's own role: only allowed to invoke this one
# Lambda. ---
resource "aws_iam_role" "scheduler_invoke" {
  name = "discord-bot-scheduler-invoke-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name = "invoke-lambda"
  role = aws_iam_role.scheduler_invoke.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.ec2_scheduler.arn
    }]
  })
}

resource "aws_scheduler_schedule" "stop" {
  name                         = "discord-bot-stop"
  schedule_expression          = var.schedule_stop_cron
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.ec2_scheduler.arn
    role_arn = aws_iam_role.scheduler_invoke.arn
    input    = jsonencode({ action = "stop" })
  }
}

resource "aws_scheduler_schedule" "start" {
  name                         = "discord-bot-start"
  schedule_expression          = var.schedule_start_cron
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.ec2_scheduler.arn
    role_arn = aws_iam_role.scheduler_invoke.arn
    input    = jsonencode({ action = "start" })
  }
}
