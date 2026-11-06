data "aws_vpc" "eks_lab" {
  filter {
    name   = "tag:Name"
    values = ["aws-eks-istio-lab-dev-vpc"]
  }
}

data "aws_subnet" "private_a" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.eks_lab.id]
  }

  filter {
    name   = "tag:Name"
    values = ["aws-eks-istio-lab-dev-private_a"]
  }
}

data "aws_eks_cluster" "lab" {
  name = "aws-eks-istio-lab-dev"
}

data "aws_ssm_parameter" "ubuntu_2404_amd64" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

module "itsm_host" {
  source = "../../modules/itsm_host"

  name_prefix                    = "cosmotech-itsm-dev"
  ami_id                         = data.aws_ssm_parameter.ubuntu_2404_amd64.value
  instance_type                  = "t3.large"
  vpc_id                         = data.aws_vpc.eks_lab.id
  vpc_cidr                       = data.aws_vpc.eks_lab.cidr_block
  subnet_id                      = data.aws_subnet.private_a.id
  eks_cluster_name               = data.aws_eks_cluster.lab.name
  additional_kubernetes_groups   = ["cosmotech:promotion"]
  bootstrap_parameter_arn_prefix = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/cosmotech/itsm/*"
  state_bucket_arn               = "arn:aws:s3:::aws-serverless-lab-414053644145-tfstate-use1"
  state_object_arn               = "arn:aws:s3:::aws-serverless-lab-414053644145-tfstate-use1/aws-serverless-lab/dev/terraform.tfstate"
  state_lock_object_arn          = "arn:aws:s3:::aws-serverless-lab-414053644145-tfstate-use1/aws-serverless-lab/dev/terraform.tfstate.tflock"
  state_key                      = "aws-serverless-lab/dev/terraform.tfstate"
  repository_url                 = "https://github.com/rlcosmo/aws-serverless-lab.git"
  tags                           = merge(local.common_tags, { System = "itsm-automation" })
}

data "aws_caller_identity" "current" {}

resource "aws_vpc_security_group_ingress_rule" "itsm_to_eks_api" {
  security_group_id            = data.aws_eks_cluster.lab.vpc_config[0].cluster_security_group_id
  referenced_security_group_id = module.itsm_host.security_group_id
  description                  = "Allow the private ITSM host to reach the EKS Kubernetes API."
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
}

output "itsm_instance_id" {
  description = "Private GLPI and Kestra host; use SSM port forwarding for its web UIs."
  value       = module.itsm_host.instance_id
}

output "itsm_private_ip" {
  description = "Private address of the ITSM host."
  value       = module.itsm_host.private_ip
}
