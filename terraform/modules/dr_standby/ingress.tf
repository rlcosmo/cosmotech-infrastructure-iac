variable "prepare_dr_tls" {
  description = "Request only the existing lab wildcard in Ohio; reuse the already verified public CNAME."
  type        = bool
  default     = false
}

resource "aws_acm_certificate" "regional" {
  count             = var.prepare_dr_tls ? 1 : 0
  domain_name       = "*.cosmotechsolutions.com.br"
  validation_method = "DNS"
  key_algorithm     = "RSA_2048"
  options {
    certificate_transparency_logging_preference = "ENABLED"
    export                                      = "DISABLED"
  }
  tags = { Name = "cosmotech-dr-regional-tls", DRTarget = "true" }
  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = var.region == "us-east-2" && var.name == "cosmotech-dr"
      error_message = "Only the isolated Ohio lab certificate may be requested."
    }
  }
}
output "regional_certificate" {
  value = try({ arn = aws_acm_certificate.regional[0].arn,
  validation_records = aws_acm_certificate.regional[0].domain_validation_options }, null)
}
