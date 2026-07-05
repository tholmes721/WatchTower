# ─────────────────────────────────────────────────────────────────────────────
# Variables
# ─────────────────────────────────────────────────────────────────────────────

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "watchtower"
}

variable "instance_type" {
  description = "EC2 instance type (t3.micro is free-tier eligible)"
  type        = string
  default     = "t3.micro"
}

variable "ssh_public_key" {
  description = "SSH public key for instance access (contents of ~/.ssh/id_rsa.pub)"
  type        = string
}

variable "allowed_cidr_blocks" {
  description = "CIDR blocks allowed SSH access (restrict to your IP for security)"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "use_elastic_ip" {
  description = "Attach an Elastic IP for a consistent address (adds ~$3.60/mo when instance is stopped)"
  type        = bool
  default     = false
}

variable "session_secret" {
  description = "Secret for session cookies (generate with: openssl rand -hex 32)"
  type        = string
  sensitive   = true
}
