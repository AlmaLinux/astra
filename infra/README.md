# Astra AWS Infrastructure (Terraform)

This folder provisions AWS infrastructure for the **astra** Django app.

## What Terraform manages

- One EC2 instance (default `t3.small`) per environment
- Aurora PostgreSQL cluster per environment (not publicly accessible)
- S3 bucket per environment for user uploads (private bucket, accessed via app credentials)
- Staging also provisions a FreeIPA EC2 instance for testing (production uses the real FreeIPA host). The
  FreeIPA security group only allows VPC-internal traffic (no public HTTP/HTTPS/SSH ingress).
- Security group for SSH + HTTP/HTTPS
- User data that installs podman and systemd units for the app + Caddy
- Uses the default VPC and its subnets

## What Terraform does NOT do

- Build/push container images
- Manage secrets outside of Terraform/Ansible (you still need to provide values in `terraform.tfvars`, which is gitignored)

## Environments and state

Environments live under:

- `infra/envs/staging`
- `infra/envs/prod`

Each environment has its own S3 backend key (separate state) in `backend.tf`.

Assumption: the S3 state bucket and DynamoDB lock table already exist:

- `almalinux-astra-terraform-state`
- `terraform-locks`

If you need Terraform to create these for a new account, run the one-time bootstrap stack in `infra/bootstrap` first.

## Providing variables (no more -var / prompts)

Each environment is designed to be applied from its folder (e.g. `infra/envs/prod`).

Terraform automatically loads variable values from a `terraform.tfvars` file in the working directory.
This repo includes per-environment templates you can copy and edit:

- `infra/envs/staging/terraform.tfvars.example`
- `infra/envs/prod/terraform.tfvars.example`

Workflow:

- Copy `terraform.tfvars.example` to `terraform.tfvars` in the same folder
- Fill in values (especially secrets like `db_password`)
- Run Terraform normally; it will pick up the values automatically

Note: `terraform.tfvars` is gitignored (all `*.tfvars` are) to avoid committing secrets.

## Systemd + podman layout

Ansible installs podman and writes systemd units from `infra/systemd`:

- `astra-app@.service` runs two app instances (ports `8001` + `8002`) with `sdnotify=container`.
- `astra-caddy.service` runs Caddy and load-balances to `127.0.0.1:8001` and `127.0.0.1:8002`.

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

On provisioning, Ansible writes/updates `/etc/astra/astra.env` and `/etc/astra/caddy.env` based on the
Terraform inputs (database endpoint, S3 settings, FreeIPA settings, etc.).

Sensitive values are passed to Ansible via a local-only Terraform `local_sensitive_file` that is
referenced from `ansible-playbook` using `-e @...json`.

This keeps secrets out of Terraform state output, shell history, and most logs.

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

The membership operations job now includes FreeIPA membership reconciliation in report mode by default and alerts
members of the configured `FREEIPA_ADMIN_GROUP` when drift or failures are detected.
