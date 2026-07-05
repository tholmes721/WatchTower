# ─────────────────────────────────────────────────────────────────────────────
# Outputs — displayed after terraform apply
# ─────────────────────────────────────────────────────────────────────────────

output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer — open this in your browser"
  value       = aws_lb.app.dns_name
}

output "alb_url" {
  description = "Full URL to access WatchTower"
  value       = "http://${aws_lb.app.dns_name}"
}

output "ecr_repository_url" {
  description = "ECR repository URL — push Docker images here"
  value       = aws_ecr_repository.app.repository_url
}

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "ECS service name"
  value       = aws_ecs_service.app.name
}

output "cloudwatch_log_group" {
  description = "CloudWatch log group for container logs"
  value       = aws_cloudwatch_log_group.app.name
}

output "vpc_id" {
  description = "VPC ID (for peering with PDU networks if needed)"
  value       = aws_vpc.main.id
}
