variable "aws_region" {
  description = "AWS region for everything in this project"
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "EC2 instance type for the k3s node. Must match CI's build architecture (x86/amd64) unless CI is changed to produce multi-arch images."
  type        = string
  default     = "t3.small"
}

variable "root_volume_size_gb" {
  description = "Root EBS volume size in GB"
  type        = number
  default     = 20
}

variable "ssh_allowed_cidr" {
  description = "CIDR allowed to SSH into the instance. Defaults to open since the tooling that manages this box has a rotating source IP; narrow this if a stable IP is available."
  type        = string
  default     = "0.0.0.0/0"
}

variable "schedule_stop_cron" {
  description = "Cron expression (EventBridge Scheduler syntax) for stopping the instance, in schedule_timezone"
  type        = string
  default     = "cron(0 7 * * ? *)"
}

variable "schedule_start_cron" {
  description = "Cron expression (EventBridge Scheduler syntax) for starting the instance, in schedule_timezone"
  type        = string
  default     = "cron(0 19 * * ? *)"
}

variable "schedule_timezone" {
  description = "IANA timezone for the stop/start schedule -- matches when the Discord server is actually active"
  type        = string
  default     = "America/Chicago"
}
