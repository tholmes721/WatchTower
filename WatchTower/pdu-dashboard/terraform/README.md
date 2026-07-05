# WatchTower — AWS ECS Deployment

Deploy WatchTower to AWS ECS Fargate with persistent storage (EFS) and a load balancer.

## Architecture

```
Internet --> ALB (port 80) --> ECS Fargate Task (port 8000)
                                      |
                                    EFS (SQLite DB)
```

## Prerequisites

1. **AWS CLI** installed and configured (`aws configure`)
2. **Terraform** >= 1.5 installed
3. **Docker** running locally (to build and push images)

## Quick Start

```bash
# 1. Navigate to terraform directory
cd terraform/

# 2. Copy and customize variables
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars — at minimum, set session_secret

# 3. Initialize Terraform
terraform init

# 4. Review what will be created
terraform plan

# 5. Create the infrastructure
terraform apply

# 6. Build and deploy the app
cd ..
./deploy.sh
```

After deploy.sh completes, open the ALB URL shown in the output.


Default login: `admin` / `watchtower`

## What Gets Created

| Resource | Purpose | Estimated Cost |
|----------|---------|----------------|
| VPC + Subnets | Networking | Free |
| NAT Gateway | Outbound internet for private subnets | ~$32/mo |
| ALB | Load balancer + health checks | ~$16/mo |
| ECS Fargate (0.5 vCPU, 1GB) | Runs the app | ~$15/mo |
| EFS | Persistent SQLite storage | ~$0.30/GB/mo |
| ECR | Docker image storage | ~$0.10/GB/mo |
| CloudWatch Logs | Container logs | ~$0.50/GB |

**Estimated total: ~$65/month** for a single-task deployment.

## Customization

Edit `terraform.tfvars`:

```hcl
# Change region
aws_region = "us-west-2"

# Restrict access to your IP only
allowed_cidr_blocks = ["203.0.113.0/32"]

# Increase resources for larger environments
task_cpu    = "1024"   # 1 vCPU
task_memory = "2048"   # 2 GB

# Required: generate a secret for session signing
session_secret = "your-64-char-hex-string-here"
```

## Updating the App

After making code changes:

```bash
./deploy.sh
```

This builds a new Docker image, pushes to ECR, and triggers a rolling
deployment. Zero downtime — ECS drains the old task after the new one is healthy.

## HTTPS Setup

1. Request a certificate in ACM for your domain
2. Uncomment the HTTPS listener section in `alb.tf`
3. Add `certificate_arn` variable to your `.tfvars`
4. Run `terraform apply`

## Connecting to PDU Networks

If your PDUs are on a separate network/VPC:
- Set up **VPC Peering** between this VPC and the PDU network
- Or use a **Transit Gateway**
- Add the PDU network CIDR to the ECS security group egress rules

The VPC ID is in the Terraform outputs for easy peering setup.

## Tear Down

```bash
terraform destroy
```

This removes ALL resources. Your SQLite data on EFS will be deleted.
