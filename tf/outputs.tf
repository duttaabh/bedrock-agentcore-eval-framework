output "ecr_repository_url" {
  description = "Push built images here (see scripts/build_and_push.sh)."
  value       = aws_ecr_repository.runtime.repository_url
}

output "opensearch_collection_endpoint" {
  description = "AOSS collection endpoint. Pass to scripts/ingest_catalog.py --endpoint."
  value       = aws_opensearchserverless_collection.catalog.collection_endpoint
}

output "opensearch_collection_name" {
  value = aws_opensearchserverless_collection.catalog.name
}

output "opensearch_index_name" {
  value = var.opensearch_index_name
}

output "agent_runtime_id" {
  value = aws_bedrockagentcore_agent_runtime.main.agent_runtime_id
}

output "agent_runtime_arn" {
  value = aws_bedrockagentcore_agent_runtime.main.agent_runtime_arn
}

output "agent_runtime_role_arn" {
  description = "Runtime execution role. If you query OpenSearch from your own machine, add that principal to additional_read_principals/additional_write_principals -- this role alone can't help you debug from a laptop."
  value       = aws_iam_role.runtime.arn
}

output "aws_account_id" {
  value = data.aws_caller_identity.current.account_id
}

output "aws_region" {
  value = var.aws_region
}
