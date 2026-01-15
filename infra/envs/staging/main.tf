provider "aws" {
  region = var.aws_region
}

data "aws_ami" "amazon_linux_2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

locals {
  name = "${var.app_name}-${var.environment}"
  tags = {
    app = var.app_name
    env = var.environment
  }

  freeipa_ingress_cidrs = []
  ansible_known_hosts_path = pathexpand(var.ansible_known_hosts_path)
  ansible_files = [
    "${path.module}/../../ansible/astra_ec2.yml",
    "${path.module}/../../systemd/astra-app@.service",
    "${path.module}/../../systemd/astra-caddy.service",
    "${path.module}/../../systemd/Caddyfile",
    "${path.module}/../../systemd/astra.env.example",
    "${path.module}/../../systemd/caddy.env.example",
    "${path.module}/../../ansible/files/deploy-prod.sh",
    "${path.module}/../../ansible/files/rollback-prod.sh",
    "${path.module}/../../ansible/files/deploy-prod-sha.sh",
  ]
  ansible_hash = sha256(join("", [for path in local.ansible_files : filesha256(path)]))
}

resource "aws_security_group" "astra" {
  name        = "${local.name}-sg"
  description = "Astra EC2 security group"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  dynamic "ingress" {
    for_each = length(var.allowed_ssh_cidrs) > 0 ? [1] : []
    content {
      description = "SSH"
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = var.allowed_ssh_cidrs
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [data.aws_vpc.default.cidr_block]
  }

  tags = merge(local.tags, {
    Name = "${local.name}-sg"
  })
}

resource "aws_instance" "astra" {
  ami                         = data.aws_ami.amazon_linux_2023.id
  instance_type               = var.instance_type
  subnet_id                   = data.aws_subnets.default.ids[0]
  vpc_security_group_ids      = [aws_security_group.astra.id]
  key_name                    = var.key_name
  associate_public_ip_address = true

  user_data = <<-EOF
#!/bin/bash
set -euo pipefail

dnf -y install python3
EOF

  tags = merge(local.tags, {
    Name = local.name
  })
}

module "freeipa" {
  source = "../../modules/freeipa"

  name_prefix     = local.name
  vpc_id          = data.aws_vpc.default.id
  subnet_id       = data.aws_subnets.default.ids[0]
  key_name        = var.key_name
  ipa_hostname    = var.freeipa_hostname
  ipa_domain      = var.freeipa_domain
  ipa_realm       = var.freeipa_realm
  ipa_admin_password = var.freeipa_admin_password
  ipa_dm_password    = var.freeipa_dm_password

  app_security_group_cidrs = [data.aws_vpc.default.cidr_block]
  allowed_ingress_cidrs    = local.freeipa_ingress_cidrs
  ssh_allowed_cidrs        = local.freeipa_ingress_cidrs
  ansible_ssh_key_path     = var.ansible_private_key_path
  ansible_user             = var.freeipa_ansible_user
  tags                     = local.tags
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db-sg"
  description = "Astra Aurora security group"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "Postgres"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.astra.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, {
    Name = "${local.name}-db-sg"
  })
}

resource "aws_db_subnet_group" "aurora" {
  name       = "${local.name}-aurora"
  subnet_ids = data.aws_subnets.default.ids

  tags = merge(local.tags, {
    Name = "${local.name}-aurora"
  })
}

resource "aws_rds_cluster" "astra" {
  cluster_identifier = "${local.name}-aurora"
  engine             = "aurora-postgresql"
  engine_version     = var.db_engine_version
  database_name      = var.db_name
  master_username    = var.db_username
  master_password    = var.db_password
  db_subnet_group_name   = aws_db_subnet_group.aurora.name
  vpc_security_group_ids = [aws_security_group.db.id]
  storage_encrypted      = true
  deletion_protection    = var.db_deletion_protection
  skip_final_snapshot    = var.db_skip_final_snapshot
  backup_retention_period = var.db_backup_retention_days
}

resource "aws_rds_cluster_instance" "astra" {
  identifier         = "${local.name}-aurora-1"
  cluster_identifier = aws_rds_cluster.astra.id
  instance_class     = var.db_instance_class
  engine             = aws_rds_cluster.astra.engine
  engine_version     = aws_rds_cluster.astra.engine_version
  publicly_accessible = false
}

resource "null_resource" "configure_instance" {
  triggers = {
    instance_id = aws_instance.astra.id
    config_hash = local.ansible_hash
    app_image   = var.app_image
    caddy_image = var.caddy_image
    cron_jobs   = jsonencode(var.cron_jobs)
  }

  provisioner "local-exec" {
    command = <<-EOT
ansible-playbook \\
  -i '${aws_instance.astra.public_ip},' \\
  -u '${var.ansible_user}' \\
  --private-key '${var.ansible_private_key_path}' \\
  -e 'app_image=${var.app_image}' \\
  -e 'caddy_image=${var.caddy_image}' \\
  -e 'django_settings_module=${var.django_settings_module}' \\
  -e 'ansible_ssh_common_args=-o UserKnownHostsFile=${local.ansible_known_hosts_path} -o StrictHostKeyChecking=yes' \\
  -e 'astra_cron_jobs=${jsonencode(var.cron_jobs)}' \\
  '${path.module}/../../ansible/astra_ec2.yml'
EOT
  }

  depends_on = [aws_instance.astra]
}
