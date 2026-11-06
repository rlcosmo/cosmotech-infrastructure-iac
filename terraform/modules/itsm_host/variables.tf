variable "name_prefix" {
  description = "Resource name prefix for the private ITSM host."
  type        = string
}

variable "ami_id" {
  description = "Ubuntu Server 24.04 LTS x86_64 AMI."
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type for GLPI and Kestra."
  type        = string
  default     = "t3.large"
}

variable "vpc_id" {
  description = "Existing laboratory VPC ID."
  type        = string
}

variable "vpc_cidr" {
  description = "Existing laboratory VPC CIDR for DNS egress."
  type        = string
}

variable "subnet_id" {
  description = "Existing private subnet ID."
  type        = string
}

variable "eks_cluster_name" {
  description = "Existing EKS cluster name."
  type        = string
}

variable "bootstrap_parameter_arn_prefix" {
  description = "SSM Parameter Store ARN prefix for the generated application admin passwords."
  type        = string
}

variable "state_bucket_arn" {
  description = "ARN of the private S3 Terraform state bucket."
  type        = string
}

variable "state_object_arn" {
  description = "Exact object ARN for the DEV Terraform state."
  type        = string
}

variable "state_lock_object_arn" {
  description = "Exact object ARN for the DEV Terraform state lock file."
  type        = string
}

variable "state_key" {
  description = "S3 key used for the DEV Terraform state."
  type        = string
}

variable "repository_url" {
  description = "Public HTTPS URL of the lab repository cloned by cloud-init."
  type        = string
}

variable "tags" {
  description = "Resource tags."
  type        = map(string)
  default     = {}
}

variable "additional_kubernetes_groups" {
  description = "Additional scoped groups on the existing access entry; preserve the backup identity."
  type        = list(string)
  default     = []
}
