# OpenSearch Serverless (AOSS) collection for the product catalog.
#
# Lexical search only (BM25 multi_match + filters) -- no vector/k-NN field, so
# this is a plain "SEARCH" collection, not "VECTORSEARCH". The index itself is
# created by scripts/ingest_catalog.py (a plain PUT mapping call), not by
# Terraform -- there's no Lambda or null_resource here, keeping this stack
# small. Re-run ingest_catalog.py any time you want to recreate the index.

locals {
  collection_name = coalesce(var.opensearch_collection_name != "" ? var.opensearch_collection_name : null, var.resource_label)

  # terraform apply runs as an assumed role (sts session); strip the session
  # suffix to get the underlying IAM role ARN so the data access policy
  # matches the role itself, not one ephemeral session.
  caller_arn_parts = split("/", data.aws_caller_identity.current.arn)
  caller_role_arn = length(local.caller_arn_parts) > 2 ? (
    "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.caller_arn_parts[length(local.caller_arn_parts) - 2]}"
  ) : data.aws_caller_identity.current.arn

  read_principals = distinct(concat(
    [aws_iam_role.runtime.arn, local.caller_role_arn],
    var.additional_read_principals,
  ))
  write_principals = distinct(concat(
    [local.caller_role_arn],
    var.additional_write_principals,
  ))
}

resource "aws_opensearchserverless_security_policy" "encryption" {
  name = local.collection_name
  type = "encryption"

  policy = jsonencode({
    Rules = [{
      ResourceType = "collection"
      Resource     = ["collection/${local.collection_name}"]
    }]
    AWSOwnedKey = true
  })
}

resource "aws_opensearchserverless_security_policy" "network" {
  name = local.collection_name
  type = "network"

  policy = jsonencode([
    {
      Rules = [
        { ResourceType = "collection", Resource = ["collection/${local.collection_name}"] },
        { ResourceType = "dashboard", Resource = ["collection/${local.collection_name}"] },
      ]
      AllowFromPublic = var.allow_public_access
    }
  ])

  lifecycle {
    precondition {
      condition     = var.allow_public_access
      error_message = "allow_public_access=false requires VPC endpoints for the collection, which this minimal stack doesn't provision. Either set allow_public_access=true, or add VPC endpoint wiring to opensearch.tf yourself."
    }
  }
}

# Data access policy: who can call AOSS data-plane APIs against this
# collection/index. This is separate from (and in addition to) the IAM
# identity policy below -- AOSS checks both layers.
resource "aws_opensearchserverless_access_policy" "collection" {
  name = local.collection_name
  type = "data"

  policy = jsonencode([
    {
      Rules = [
        {
          ResourceType = "index"
          Resource     = ["index/${local.collection_name}/*"]
          Permission   = ["aoss:DescribeIndex", "aoss:ReadDocument"]
        },
        {
          ResourceType = "collection"
          Resource     = ["collection/${local.collection_name}"]
          Permission   = ["aoss:DescribeCollectionItems"]
        },
      ]
      Principal = local.read_principals
    },
    {
      Rules = [
        {
          ResourceType = "index"
          Resource     = ["index/${local.collection_name}/*"]
          Permission = [
            "aoss:CreateIndex", "aoss:DeleteIndex", "aoss:DescribeIndex",
            "aoss:ReadDocument", "aoss:UpdateIndex", "aoss:WriteDocument",
          ]
        },
        {
          ResourceType = "collection"
          Resource     = ["collection/${local.collection_name}"]
          Permission = [
            "aoss:CreateCollectionItems", "aoss:DeleteCollectionItems",
            "aoss:DescribeCollectionItems", "aoss:UpdateCollectionItems",
          ]
        },
      ]
      Principal = local.write_principals
    },
  ])
}

resource "aws_opensearchserverless_collection" "catalog" {
  name = local.collection_name
  type = "SEARCH"

  depends_on = [
    aws_opensearchserverless_access_policy.collection,
    aws_opensearchserverless_security_policy.encryption,
    aws_opensearchserverless_security_policy.network,
  ]
}

# AOSS access policies take a few seconds to propagate; give ingest/queries
# issued right after `apply` a head start instead of racing them.
resource "time_sleep" "wait_for_access_policy" {
  create_duration = "20s"

  depends_on = [
    aws_opensearchserverless_access_policy.collection,
    aws_opensearchserverless_collection.catalog,
  ]
}
