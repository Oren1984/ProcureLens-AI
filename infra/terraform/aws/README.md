# Optional AWS single-host demo (Terraform)

This creates one EC2 host that runs the same Docker Compose stack. **Status: the code passes `terraform fmt` and `terraform validate`; it has not been applied, and no deployment has been verified.**

## What it creates

| Resource | Purpose | Billable |
|---|---|---|
| VPC, public subnet, IGW, route table | minimal network | no direct charge |
| Security group | **no inbound** by default; optional 443 from `allowed_https_cidrs` (0.0.0.0/0 rejected); no SSH, no 5432 | no |
| EC2 `t3.medium` (default), IMDSv2 only, hop limit 1 | runs Compose | yes (hourly) |
| Encrypted gp3 root (30 GB) + encrypted gp3 data volume (20 GB) at `/srv/procurelens` | OS, Docker data, backups | yes (per GB-month) |
| Public IPv4 address | outbound access for packages, images and SSM | yes (hourly) |
| IAM role with `AmazonSSMManagedInstanceCore` only | Session Manager access without SSH | no |
| DLM policy: daily data-volume snapshots, 7 kept (`snapshot_retention_days`, 0 disables) | backups | yes (snapshot storage) |

Costs depend on region and usage; check the AWS pricing pages. No cost is guaranteed here.

## Deploy (manual, by an operator)

```bash
cd infra/terraform/aws
terraform init
terraform plan -out tf.plan          # review it
terraform apply tf.plan
```

Then connect without opening any port:

```bash
aws ssm start-session --target <instance_id>                                  # shell
$(terraform output -raw ssm_port_forward_command)                             # UI at http://localhost:8089
```

On the host, as a user in the `docker` group:

```bash
git clone <your repo> /srv/procurelens/app && cd /srv/procurelens/app
./scripts/init-env.sh /srv/procurelens/private     # secrets generated ON the host
docker compose up -d --build
```

## Secrets

No secret is placed in Terraform variables, outputs, state or user data. `user_data.sh` only installs Docker and mounts the data volume. Database passwords are generated on the host by `init-env.sh`. ERPNext API credentials are typed into the host's `.env` (mode 0600) over an SSM session, or stored in SSM Parameter Store as `SecureString` and fetched manually. Fetching them automatically would need an extra, narrowly scoped IAM permission, which is not included. The bootstrap credentials file stays in `/srv/procurelens/private` (0700, UID 10001).

## TLS before remote use

The app serves plain HTTP on `127.0.0.1:8089` on the host. Two supported options:

1. **Recommended: no public ingress.** Use the SSM port-forwarding command above. The transport is encrypted by Session Manager and the security group stays closed.
2. **HTTPS for a small, approved audience.** Set `allowed_https_cidrs`, point a DNS name at the instance, and run a reverse proxy on the host (for example Caddy: `procurelens.example.com { reverse_proxy 127.0.0.1:8089 }`). Port 80 is closed, so obtain certificates with the DNS-01 challenge. Keep Streamlit bound to loopback.

## Persistence, backup and teardown

- Docker data (PostgreSQL volume, model cache) lives on the encrypted data volume. Daily snapshots via DLM, plus `./scripts/backup.sh` for logical dumps (copy them off-host privately).
- Replacing the instance keeps the data volume only if you re-attach it; `terraform destroy` deletes it.
- **Teardown:** take a final `./scripts/backup.sh` if needed, then `terraform destroy`. DLM snapshots created by the policy are not deleted by `destroy`; remove them in the EC2 console (Snapshots, tag `Snapshot=procurelens-demo`) to stop storage charges.
