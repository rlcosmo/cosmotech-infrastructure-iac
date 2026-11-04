locals {
  public_observability_hosts = [
    "prometheus-aws-eks-istio-lab-dev.cosmotechsolutions.com.br",
    "alertmanager.cosmotechsolutions.com.br",
    "tempo.cosmotechsolutions.com.br",
  ]
}

data "aws_security_group" "observability_alb" {
  filter {
    name   = "group-name"
    values = ["k8s-observability-6c81249268"]
  }

  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.eks_lab.id]
  }
}

data "aws_lb" "observability" {
  name = "k8s-observability-633f55bc55"
}

data "aws_lb_listener" "observability_https" {
  load_balancer_arn = data.aws_lb.observability.arn
  port              = 443
}

resource "aws_vpc_security_group_ingress_rule" "itsm_from_observability_alb" {
  security_group_id            = module.itsm_host.security_group_id
  referenced_security_group_id = data.aws_security_group.observability_alb.id
  description                  = "Allow the existing public ALB to reach the private ITSM reverse proxy."
  from_port                    = 8088
  to_port                      = 8088
  ip_protocol                  = "tcp"
}

resource "aws_lb_target_group" "itsm_public_proxy" {
  name                 = "cosmo-itsm-public-proxy"
  port                 = 8088
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = data.aws_vpc.eks_lab.id
  deregistration_delay = 30

  health_check {
    enabled             = true
    protocol            = "HTTP"
    port                = "traffic-port"
    path                = "/healthz"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = merge(local.common_tags, { System = "itsm-public-proxy" })
}

resource "aws_lb_target_group_attachment" "itsm_public_proxy" {
  target_group_arn = aws_lb_target_group.itsm_public_proxy.arn
  target_id        = module.itsm_host.private_ip
  port             = 8088
}

# Listener rules belong to the ALB controller's observability IngressGroup.
# See ops/kubernetes/public/management-tools-ingress.yaml. The former Terraform
# listener rule was deleted by controller reconciliation on 2026-11-04.
removed {
  from = aws_lb_listener_rule.itsm_public
  lifecycle {
    destroy = false
  }
}

resource "aws_wafv2_ip_set" "observability_admin" {
  name               = "cosmotech-observability-admin"
  description        = "Administrator source address for existing observability dashboards."
  scope              = "REGIONAL"
  ip_address_version = "IPV4"
  # Additional Wi-Fi address authorized by the owner; this does not expand EKS API access.
  addresses = distinct(concat(
    tolist(data.aws_eks_cluster.lab.vpc_config[0].public_access_cidrs),
    ["168.121.24.180/32"],
  ))
  tags = merge(local.common_tags, { System = "public-ingress" })
}

resource "aws_wafv2_web_acl" "public_ingress" {
  name        = "cosmotech-public-ingress"
  description = "Expose lab web tools by hostname with per-IP rate limits."
  scope       = "REGIONAL"

  default_action {
    block {}
  }

  rule {
    name     = "rate-limit-public-itsm"
    priority = 0

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 1200
        aggregate_key_type = "IP"

        scope_down_statement {
          or_statement {
            statement {
              byte_match_statement {
                search_string         = "glpi.cosmotechsolutions.com.br"
                positional_constraint = "EXACTLY"
                field_to_match {
                  single_header {
                    name = "host"
                  }
                }
                text_transformation {
                  priority = 0
                  type     = "LOWERCASE"
                }
              }
            }
            statement {
              byte_match_statement {
                search_string         = "kestra.cosmotechsolutions.com.br"
                positional_constraint = "EXACTLY"
                field_to_match {
                  single_header {
                    name = "host"
                  }
                }
                text_transformation {
                  priority = 0
                  type     = "LOWERCASE"
                }
              }
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "cosmotech-public-itsm-rate-limit"
      sampled_requests_enabled   = false
    }
  }

  rule {
    name     = "allow-public-itsm-hostnames"
    priority = 1

    action {
      allow {}
    }

    statement {
      or_statement {
        statement {
          byte_match_statement {
            search_string         = "glpi.cosmotechsolutions.com.br"
            positional_constraint = "EXACTLY"
            field_to_match {
              single_header {
                name = "host"
              }
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
        statement {
          byte_match_statement {
            search_string         = "kestra.cosmotechsolutions.com.br"
            positional_constraint = "EXACTLY"
            field_to_match {
              single_header {
                name = "host"
              }
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "cosmotech-public-itsm-hosts"
      sampled_requests_enabled   = false
    }
  }

  # Grafana enforces its own authentication. Other dashboard hosts stay private.
  rule {
    name     = "rate-limit-public-grafana"
    priority = 3
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = 1200
        aggregate_key_type = "IP"
        scope_down_statement {
          byte_match_statement {
            search_string         = "grafana.cosmotechsolutions.com.br"
            positional_constraint = "EXACTLY"
            field_to_match {
              single_header { name = "host" }
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "cosmotech-grafana-rate-limit"
      sampled_requests_enabled   = false
    }
  }
  rule {
    name     = "allow-public-grafana"
    priority = 4
    action {
      allow {}
    }
    statement {
      byte_match_statement {
        search_string         = "grafana.cosmotechsolutions.com.br"
        positional_constraint = "EXACTLY"
        field_to_match {
          single_header { name = "host" }
        }
        text_transformation {
          priority = 0
          type     = "LOWERCASE"
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "cosmotech-public-grafana"
      sampled_requests_enabled   = false
    }
  }
  rule {
    name     = "rate-limit-public-argocd"
    priority = 5
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = 1200
        aggregate_key_type = "IP"
        scope_down_statement {
          byte_match_statement {
            search_string         = "argocd.cosmotechsolutions.com.br"
            positional_constraint = "EXACTLY"
            field_to_match {
              single_header { name = "host" }
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "cosmotech-argocd-rate-limit"
      sampled_requests_enabled   = false
    }
  }
  rule {
    name     = "allow-public-argocd"
    priority = 6
    action {
      allow {}
    }
    statement {
      byte_match_statement {
        search_string         = "argocd.cosmotechsolutions.com.br"
        positional_constraint = "EXACTLY"
        field_to_match {
          single_header { name = "host" }
        }
        text_transformation {
          priority = 0
          type     = "LOWERCASE"
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "cosmotech-public-argocd"
      sampled_requests_enabled   = false
    }
  }
  rule {
    name     = "rate-limit-public-observability"
    priority = 7

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 1200
        aggregate_key_type = "IP"
        scope_down_statement {
          or_statement {
            dynamic "statement" {
              for_each = local.public_observability_hosts
              content {
                byte_match_statement {
                  search_string         = statement.value
                  positional_constraint = "EXACTLY"
                  field_to_match {
                    single_header { name = "host" }
                  }
                  text_transformation {
                    priority = 0
                    type     = "LOWERCASE"
                  }
                }
              }
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "cosmotech-observability-rate-limit"
      sampled_requests_enabled   = false
    }
  }
  rule {
    name     = "allow-public-observability"
    priority = 8

    action {
      allow {}
    }

    statement {
      or_statement {
        dynamic "statement" {
          for_each = local.public_observability_hosts
          content {
            byte_match_statement {
              search_string         = statement.value
              positional_constraint = "EXACTLY"
              field_to_match {
                single_header { name = "host" }
              }
              text_transformation {
                priority = 0
                type     = "LOWERCASE"
              }
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "cosmotech-public-observability"
      sampled_requests_enabled   = false
    }
  }

  rule {
    name     = "orders-dev-rate"
    priority = 20
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = 1200
        aggregate_key_type = "IP"
        scope_down_statement {
          regex_match_statement {
            regex_string = "^orders-(api|worker)-dev[.]cosmotechsolutions[.]com[.]br$"
            field_to_match {
              single_header { name = "host" }
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "cosmotech-orders-dev-rate"
      sampled_requests_enabled   = false
    }
  }

  rule {
    name     = "orders-dev-allow"
    priority = 21
    action {
      allow {}
    }
    statement {
      regex_match_statement {
        regex_string = "^orders-(api|worker)-dev[.]cosmotechsolutions[.]com[.]br$"
        field_to_match {
          single_header { name = "host" }
        }
        text_transformation {
          priority = 0
          type     = "LOWERCASE"
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "cosmotech-orders-dev-allow"
      sampled_requests_enabled   = false
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "cosmotech-public-ingress"
    sampled_requests_enabled   = false
  }

  tags = merge(local.common_tags, { System = "public-ingress" })
}

resource "aws_cloudwatch_log_group" "waf_public_ingress" {
  name              = "aws-waf-logs-cosmotech-public-ingress"
  retention_in_days = 30
  tags              = merge(local.common_tags, { System = "public-ingress" })
}

resource "aws_wafv2_web_acl_logging_configuration" "public_ingress" {
  resource_arn            = aws_wafv2_web_acl.public_ingress.arn
  log_destination_configs = [aws_cloudwatch_log_group.waf_public_ingress.arn]

  redacted_fields {
    single_header {
      name = "authorization"
    }
  }

  redacted_fields {
    single_header {
      name = "cookie"
    }
  }

  redacted_fields {
    query_string {}
  }

  redacted_fields {
    uri_path {}
  }
}

resource "aws_wafv2_web_acl_association" "observability_alb" {
  resource_arn = data.aws_lb.observability.arn
  web_acl_arn  = aws_wafv2_web_acl.public_ingress.arn
}

output "public_ingress_web_acl_arn" {
  description = "AWS WAF Web ACL protecting the shared public observability and ITSM ALB."
  value       = aws_wafv2_web_acl.public_ingress.arn
}

output "glpi_url" {
  description = "Public GLPI URL."
  value       = "https://glpi.cosmotechsolutions.com.br/"
}

output "kestra_url" {
  description = "Public Kestra URL."
  value       = "https://kestra.cosmotechsolutions.com.br/"
}

output "itsm_public_target_group_arn" {
  description = "Private EC2 reverse-proxy target group behind the protected public ALB."
  value       = aws_lb_target_group.itsm_public_proxy.arn
}
