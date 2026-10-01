# Terraform/provider setup for the AgentCore evaluation runtime.
#
# No backend block -> local state by default. Point this at a remote backend
# (S3, etc.) yourself if you want shared state; this repo is meant to be
# small enough that local state is a reasonable default.

terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.41"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.13"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile != "" ? var.aws_profile : null

  default_tags {
    tags = {
      Project   = "agentcore-runtime-eval-framework"
      ManagedBy = "Terraform"
    }
  }
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}
