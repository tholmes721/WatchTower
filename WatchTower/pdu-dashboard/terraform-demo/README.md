# WatchTower Demo — Single EC2 Instance

A cost-effective deployment for demos. Start the instance when you need it,
stop it when you're done. Pay only for the hours it runs.

## Cost Breakdown

| State | Cost |
|-------|------|
| Running (t3.micro) | ~$0.0104/hour (~$7.50 if 24/7) |
| Stopped (EBS only) | ~$1.60/month (20GB gp3) |
| Elastic IP (optional) | +$3.60/month when NOT attached to running instance |

**Typical demo use (10 hours/month): ~$2/month**

## Prerequisites

1. AWS CLI configured (`aws configure`)
2. Terraform >= 1.5 installed
3. An SSH key pair (`ssh-keygen` if you don't have one)

## Deploy

```bash
cd terraform-demo/
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars — add your SSH key and session secret

terraform init
terraform apply
```

Wait 2-3 minutes for the instance to boot and install Docker. Then open the
URL from the output.

**Default login:** admin / watchtower

## Daily Use

```bash
# Before a demo — start the instance (~30 seconds to boot)
aws ec2 start-instances --instance-ids i-0abc123def456 --region us-east-1

# After the demo — stop to save money
aws ec2 stop-instances --instance-ids i-0abc123def456 --region us-east-1
```

The instance ID and ready-to-paste commands are shown in the Terraform outputs.

## Notes

- **Data persists** across stop/start (stored on EBS volume)
- **IP changes** each time you start (unless you enable Elastic IP)
- WatchTower **auto-starts** when the instance boots (systemd service)
- **Setup log:** SSH in and check `/var/log/watchtower-setup.log`
- **Update the app:** SSH in, `cd /opt/WatchTower/WatchTower/pdu-dashboard && git pull && docker-compose up -d --build`

## Tear Down

```bash
terraform destroy
```

This deletes the instance AND the EBS volume (all data lost).
