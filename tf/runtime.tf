# AgentCore Runtime + endpoint. No Cognito authorizer configured, so the
# runtime is invoked with plain SigV4/IAM auth -- whoever can assume a role
# allowed to call bedrock-agentcore:InvokeAgentRuntime can hit it directly.
# That's deliberate for a standalone eval tool: scripts/eval/run_eval.py's
# --runtime-id path signs requests with your local AWS credentials.

resource "aws_bedrockagentcore_agent_runtime" "main" {
  agent_runtime_name = replace(var.resource_label, "-", "_")
  description        = "Product search/discovery AgentCore eval runtime (${var.resource_label})"
  role_arn           = aws_iam_role.runtime.arn

  depends_on = [
    aws_iam_role_policy.ecr_access,
    aws_iam_role_policy.cloudwatch_logs,
    aws_iam_role_policy.bedrock_access,
    aws_iam_role_policy.opensearch_access,
    time_sleep.wait_for_access_policy,
  ]

  agent_runtime_artifact {
    container_configuration {
      container_uri = "${aws_ecr_repository.runtime.repository_url}:${var.image_tag}"
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  environment_variables = {
    AWS_REGION             = var.aws_region
    AGENT_MODEL_ID         = var.default_agent_model_id
    ALLOW_CONFIG_OVERRIDES = tostring(var.allow_config_overrides)
    OPENSEARCH_ENDPOINT    = aws_opensearchserverless_collection.catalog.collection_endpoint
    OPENSEARCH_INDEX       = var.opensearch_index_name
    LOG_LEVEL              = "INFO"
  }

  # Image rollouts happen out-of-band (build_and_push.sh + a manual
  # update-agent-runtime call, or re-apply with a new image_tag); Terraform
  # creates the runtime once and leaves the artifact alone afterwards so
  # destroy-then-create races never collide with Bedrock's name-uniqueness
  # rule.
  lifecycle {
    ignore_changes = [agent_runtime_artifact]
  }
}

resource "aws_bedrockagentcore_agent_runtime_endpoint" "main" {
  name             = replace("${var.resource_label}_endpoint", "-", "_")
  agent_runtime_id = aws_bedrockagentcore_agent_runtime.main.agent_runtime_id
  description      = "Endpoint for ${var.resource_label}"
}
