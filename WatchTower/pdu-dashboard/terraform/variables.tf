# ─────────────────────────────────────────────────────────────────────────────
# Variables — customize these for your deployment
# ─────────────────────────────────────────────────────────────────────────────

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "watchtower"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "allowed_cidr_blocks" {
  description = "CIDR blocks allowed to access the ALB (use [\"0.0.0.0/0\"] for public, or restrict to your IP)"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "task_cpu" {
  description = "Fargate task CPU units (256 = 0.25 vCPU, 512 = 0.5, 1024 = 1)"
  type        = string
  default     = "512"
}

variable "task_memory" {
  description = "Fargate task memory in MB"
  type        = string
  default     = "1024"
}

variable "desired_count" {
  description = "Number of ECS tasks to run (use 1 for SQLite)"
  type        = number
  default     = 1
}

variable "session_secret" {
  description = "Secret key for signing session cookies (generate with: openssl rand -hex 32)"
  type        = string
  sensitive   = true
}

# variable "certificate_arn" {
#   description = "ACM certificate ARN for HTTPS (uncomment alb.tf HTTPS listener to use)"
#   type        = string
#   default     = ""
# }
