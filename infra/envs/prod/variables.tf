variable "aws_region" {
  type    = string
  default = "eu-west-1"
}

variable "app_name" {
  type        = string
  description = "Application name."
  default     = "astra"
}

variable "environment" {
  type        = string
  description = "Environment name."
  default     = "prod"
}

variable "instance_type" {
  type        = string
  description = "EC2 instance type."
  default     = "t3.small"
}

variable "key_name" {
  type        = string
  description = "EC2 key pair name."
}

variable "allowed_ssh_cidrs" {
  type        = list(string)
  description = "CIDR blocks allowed to access SSH."
  default     = []
}

variable "app_image" {
  type        = string
  description = "Container image for the Astra app."
}

variable "caddy_image" {
  type        = string
  description = "Container image for Caddy."
}

variable "s3_bucket_name" {
  type        = string
  description = "S3 bucket name for user uploads."
}

variable "s3_force_destroy" {
  type        = bool
  description = "Force destroy S3 bucket on delete."
  default     = false
}

variable "db_name" {
  type        = string
  description = "Database name for Aurora."
}

variable "db_username" {
  type        = string
  description = "Database master username for Aurora."
}

variable "db_password" {
  type        = string
  sensitive   = true
  description = "Database master password for Aurora."
}

variable "db_engine_version" {
  type        = string
  description = "Aurora Postgres engine version."
  default     = "15.4"
}

variable "db_instance_class" {
  type        = string
  description = "Aurora instance class."
  default     = "db.t3.medium"
}

variable "db_backup_retention_days" {
  type        = number
  description = "Number of days to retain Aurora backups."
  default     = 7
}

variable "db_deletion_protection" {
  type        = bool
  description = "Enable deletion protection for Aurora."
  default     = true
}

variable "db_skip_final_snapshot" {
  type        = bool
  description = "Skip final snapshot on Aurora deletion."
  default     = false
}

variable "ansible_user" {
  type        = string
  description = "SSH user for Ansible."
  default     = "ec2-user"
}

variable "ansible_private_key_path" {
  type        = string
  description = "Path to SSH private key for Ansible."
}

variable "ansible_known_hosts_path" {
  type        = string
  description = "Path to SSH known_hosts for Ansible."
  default     = "~/.ssh/known_hosts"
}

variable "django_settings_module" {
  type        = string
  description = "DJANGO_SETTINGS_MODULE value for the env file."
  default     = "config.settings"
}

variable "secret_key" {
  type        = string
  sensitive   = true
  description = "Django SECRET_KEY for production. If empty, provisioning generates one on the host."
  default     = ""
}

variable "allowed_hosts" {
  type        = list(string)
  description = "Django ALLOWED_HOSTS. If empty, provisioning derives a safe default from instance metadata."
  default     = []
}

variable "public_base_url" {
  type        = string
  description = "PUBLIC_BASE_URL (used for absolute links in email)."
  default     = ""
}

variable "default_from_email" {
  type        = string
  description = "DEFAULT_FROM_EMAIL used by Django."
  default     = ""
}

variable "freeipa_host" {
  type        = string
  description = "FreeIPA host for the app to connect to (hostname preferred)."
}

variable "freeipa_verify_ssl" {
  type        = bool
  description = "Whether the app should verify FreeIPA TLS certificates."
  default     = true
}

variable "freeipa_service_user" {
  type        = string
  description = "FreeIPA service account username used by the app."
  default     = "admin"
}

variable "freeipa_service_password" {
  type        = string
  sensitive   = true
  description = "FreeIPA service account password used by the app."
}

variable "django_auto_migrate" {
  type        = bool
  description = "If true, containers run migrate on startup (entrypoint DJANGO_AUTO_MIGRATE=1)."
  default     = true
}

variable "django_migrate_retries" {
  type        = number
  description = "How many times to retry migrations on startup."
  default     = 30
}

variable "cron_jobs" {
  type = list(object({
    name    = string
    command = string
    minute  = optional(string)
    hour    = optional(string)
    day     = optional(string)
    month   = optional(string)
    weekday = optional(string)
  }))
  description = "Cron jobs to configure on the host."
  default = [
    {
      name    = "operations-hourly"
      minute  = "0"
      command = "podman exec astra-app-1 python manage.py operations_hourly"
    },
    {
      name    = "operations-daily"
      minute  = "0"
      hour    = "0"
      command = "podman exec astra-app-1 python manage.py operations_daily"
    }
  ]
}
