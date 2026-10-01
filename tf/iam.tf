# IAM role assumed by the AgentCore Runtime service to run the container.

locals {
  bedrock_arn_entries = [for m in var.allowed_bedrock_models : m if startswith(m, "arn:")]
  bedrock_id_entries  = [for m in var.allowed_bedrock_models : m if !startswith(m, "arn:")]
  bedrock_model_resources = length(var.allowed_bedrock_models) == 0 ? [
    "arn:aws:bedrock:*::foundation-model/*",
    "arn:aws:bedrock:*:*:inference-profile/*",
    ] : concat(
    local.bedrock_arn_entries,
    [for m in local.bedrock_id_entries : "arn:aws:bedrock:*:*:inference-profile/${m}"],
    [for m in local.bedrock_id_entries : "arn:aws:bedrock:*::foundation-model/${replace(m, "/^(us|us-gov|eu|apac|au|jp|global)\\./", "")}"],
  )
}

data "aws_iam_policy_document" "assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "runtime" {
  name               = "${var.resource_label}-runtime-role"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

data "aws_iam_policy_document" "ecr_access" {
  statement {
    sid       = "ECRAuthToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid       = "ECRImageAccess"
    effect    = "Allow"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [aws_ecr_repository.runtime.arn]
  }
}

resource "aws_iam_role_policy" "ecr_access" {
  name   = "ecr-access"
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.ecr_access.json
}

data "aws_iam_policy_document" "cloudwatch_logs" {
  statement {
    sid    = "LogsWrite"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = [
      "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/bedrock-agentcore/*",
    ]
  }
  statement {
    sid       = "LogsDescribeAll"
    effect    = "Allow"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:*"]
  }
}

resource "aws_iam_role_policy" "cloudwatch_logs" {
  name   = "cloudwatch-logs"
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.cloudwatch_logs.json
}

data "aws_iam_policy_document" "bedrock_access" {
  statement {
    sid    = "BedrockInvokeModel"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = local.bedrock_model_resources
  }
}

resource "aws_iam_role_policy" "bedrock_access" {
  name   = "bedrock-access"
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.bedrock_access.json
}

# Identity-based half of AOSS authorization. The data access policy in
# opensearch.tf is the other half -- AOSS requires both.
data "aws_iam_policy_document" "opensearch_access" {
  statement {
    sid       = "AossApiAccess"
    effect    = "Allow"
    actions   = ["aoss:APIAccessAll"]
    resources = [aws_opensearchserverless_collection.catalog.arn]
  }
}

resource "aws_iam_role_policy" "opensearch_access" {
  name   = "opensearch-access"
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.opensearch_access.json
}
