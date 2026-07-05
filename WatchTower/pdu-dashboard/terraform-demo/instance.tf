# ─────────────────────────────────────────────────────────────────────────────
# EC2 Instance — Amazon Linux 2023 with Docker + WatchTower
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_instance" "demo" {
  ami           = data.aws_ami.amazon_linux_2023.id
  instance_type = var.instance_type
  key_name      = aws_key_pair.demo.key_name

  vpc_security_group_ids = [aws_security_group.demo.id]

  # 20 GB root volume (persists when instance is stopped)
  root_block_device {
    volume_size           = 20
    volume_type           = "gp3"
    delete_on_termination = false
    encrypted             = true

    tags = {
      Name = "${var.project_name}-demo-volume"
    }
  }

  # User data script — runs on FIRST boot only
  user_data = base64encode(templatefile("${path.module}/userdata.sh", {
    session_secret = var.session_secret
  }))

  tags = {
    Name = "${var.project_name}-demo"
  }
}

# ── Elastic IP (optional — keeps a consistent public IP) ─────────────────────

resource "aws_eip" "demo" {
  count    = var.use_elastic_ip ? 1 : 0
  instance = aws_instance.demo.id
  domain   = "vpc"

  tags = {
    Name = "${var.project_name}-demo-eip"
  }
}
