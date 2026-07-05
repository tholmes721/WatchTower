# ─────────────────────────────────────────────────────────────────────────────
# Outputs
# ─────────────────────────────────────────────────────────────────────────────

output "instance_id" {
  description = "EC2 instance ID (use for start/stop commands)"
  value       = aws_instance.demo.id
}

output "public_ip" {
  description = "Public IP address (changes on stop/start unless using Elastic IP)"
  value       = var.use_elastic_ip ? aws_eip.demo[0].public_ip : aws_instance.demo.public_ip
}

output "watchtower_url" {
  description = "WatchTower URL — open in browser"
  value       = "http://${var.use_elastic_ip ? aws_eip.demo[0].public_ip : aws_instance.demo.public_ip}:8000"
}

output "ssh_command" {
  description = "SSH into the instance"
  value       = "ssh ec2-user@${var.use_elastic_ip ? aws_eip.demo[0].public_ip : aws_instance.demo.public_ip}"
}

output "start_command" {
  description = "Start the instance (before a demo)"
  value       = "aws ec2 start-instances --instance-ids ${aws_instance.demo.id} --region ${var.aws_region}"
}

output "stop_command" {
  description = "Stop the instance (after a demo — saves money)"
  value       = "aws ec2 stop-instances --instance-ids ${aws_instance.demo.id} --region ${var.aws_region}"
}
