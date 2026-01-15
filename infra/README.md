# Astra AWS Infrastructure (Terraform)

This folder provisions AWS infrastructure for the **astra** Django app.

## What Terraform manages

- One EC2 instance (default `t3.small`) per environment
- Aurora PostgreSQL cluster per environment (not publicly accessible)
- Staging also provisions a FreeIPA EC2 instance for testing (production uses the real FreeIPA host). The
  FreeIPA security group only allows VPC-internal traffic (no public HTTP/HTTPS/SSH ingress).
- Security group for SSH + HTTP/HTTPS
- User data that installs podman and systemd units for the app + Caddy
- Uses the default VPC and its subnets

## What Terraform does NOT do

- Build/push container images
- Configure application secrets (these live in `/etc/astra/astra.env` on the host, including the Aurora connection string)

## Environments and state

Environments live under:

- `infra/envs/staging`
- `infra/envs/prod`

Each environment has its own S3 backend key (separate state) in `backend.tf`.

Assumption: the S3 state bucket and DynamoDB lock table already exist:

- `almalinux-astra-terraform-state`
- `terraform-locks`

If you need Terraform to create these for a new account, run the one-time bootstrap stack in `infra/bootstrap` first.

## Systemd + podman layout

Ansible installs podman and writes systemd units from `infra/systemd`:

- `astra-app@.service` runs two app instances (ports `8001` + `8002`) with `sdnotify=container`.
- `astra-caddy.service` runs Caddy and load-balances to `localhost:8001` and `localhost:8002`.

## Ansible provisioning

Terraform launches the EC2 instance and then runs `infra/ansible/astra_ec2.yml` via a local-exec
provisioner. You must provide the SSH private key path and (optionally) the SSH user in the env
variables:

- `ansible_private_key_path` (required)
- `ansible_user` (defaults to `ec2-user`)
- `ansible_known_hosts_path` (defaults to `~/.ssh/known_hosts`; ensure the host key is present)

The playbook installs podman, copies the systemd units and Caddyfile, writes `/etc/astra/astra.env`
(if missing), and installs the deployment scripts under `/usr/local/bin`.

## Environment file updates

On first boot, `/etc/astra/astra.env` is created with `APP_IMAGE` plus empty placeholders for required
application settings. `/etc/astra/caddy.env` is created with `CADDY_IMAGE`. Copy
`infra/systemd/astra.env.example` and `infra/systemd/caddy.env.example`, fill in the values, and upload
them to `/etc/astra/astra.env` and `/etc/astra/caddy.env`.

After updating the env file on the host:

```bash
sudo systemctl restart astra-app@1.service astra-app@2.service astra-caddy.service
```

## Deployment scripts

Ansible installs the following scripts:

- `/usr/local/bin/deploy-prod.sh` (pull latest image, run migrations, restart app instances in order)
- `/usr/local/bin/rollback-prod.sh` (roll back to the previous digest stored in `/etc/astra/last_app_image`)
- `/usr/local/bin/deploy-prod-sha.sh <sha256|sha256:hash|image@sha256:hash>` (deploy a specific digest)

## Cron jobs

Define cron jobs in Terraform using the `cron_jobs` variable. Example:

```hcl
cron_jobs = [
  {
    name    = "membership-operations"
    minute  = "0"
    hour    = "0"
    command = "podman exec astra-app-1 python manage.py membership_operations"
  }
]
```

If `minute` or `hour` are omitted, they default to `0` (midnight local time).
