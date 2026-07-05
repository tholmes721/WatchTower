# ─────────────────────────────────────────────────────────────────────────────
# EFS — Persistent file system for SQLite database
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_efs_file_system" "app_data" {
  creation_token = "${var.project_name}-data"
  encrypted      = true

  performance_mode = "generalPurpose"
  throughput_mode  = "bursting"

  tags = {
    Name = "${var.project_name}-data"
  }
}

# Mount targets in each private subnet
resource "aws_efs_mount_target" "app_data" {
  count           = 2
  file_system_id  = aws_efs_file_system.app_data.id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.efs.id]
}

# Access point — sets the directory, UID/GID for the container
resource "aws_efs_access_point" "app_data" {
  file_system_id = aws_efs_file_system.app_data.id

  posix_user {
    uid = 1000
    gid = 1000
  }

  root_directory {
    path = "/watchtower-data"
    creation_info {
      owner_uid   = 1000
      owner_gid   = 1000
      permissions = "755"
    }
  }

  tags = {
    Name = "${var.project_name}-access-point"
  }
}
