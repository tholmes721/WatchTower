# ─────────────────────────────────────────────────────────────────────────────
# WatchTower Demo — Single EC2 Instance (Amazon Linux 2023)
#
# Cost: ~$3-5/month (only while running)
# Start/stop on demand for demos
# ─────────────────────────────────────────────────────────────────────────────

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ── Latest Amazon Linux 2023 AMI ─────────────────────────────────────────────

data "aws_ami" "amazon_linux_2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# ── SSH Key Pair ─────────────────────────────────────────────────────────────

resource "aws_key_pair" "demo" {
  key_name   = "${var.project_name}-demo-key"
  public_key = var.ssh_public_key
}

# ── Security Group ───────────────────────────────────────────────────────────

resource "aws_security_group" "demo" {
  name_prefix = "${var.project_name}-demo-"
  description = "WatchTower demo instance"

  # SSH access
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr_blocks
    description = "SSH"
  }

  # WatchTower web UI
  ingress {
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "WatchTower HTTP"
  }

  # Outbound (pull Docker images, poll PDUs)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${var.project_name}-demo-sg"
  }
}
