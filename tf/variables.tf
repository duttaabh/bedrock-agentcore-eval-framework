variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-west-2"
}

variable "aws_profile" {
  description = "Named AWS CLI profile to use. Empty string uses the default credential chain."
  type        = string
  default     = ""
}

variable "resource_label" {
  description = "Short label used to name/tag everything this stack creates (ECR repo, AOSS collection, IAM role, runtime name)."
  type        = string
  default     = "agentcore-eval"
}

variable "image_tag" {
  description = "Tag of the runtime image in ECR to activate on the AgentCore Runtime. Build/push happens outside Terraform (see scripts/build_and_push.sh); Terraform only wires the runtime to whatever tag you give it."
  type        = string
  default     = "latest"
}

variable "default_agent_model_id" {
  description = "Default Bedrock model ID the runtime uses when a request doesn't send config_overrides.agent_model_id. Any model you want to be able to switch to at eval time must also be covered by allowed_bedrock_models below."
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "allow_config_overrides" {
  description = "When true, callers can override agent_model_id (and a couple of other knobs) per-request via the config_overrides field in the invocation body. This is what lets scripts/eval/run_eval.py's --agent-model / --compare-models switch models without redeploying. Leave true for an eval environment; an environment meant to pin one model should set this false."
  type        = bool
  default     = true
}

variable "allowed_bedrock_models" {
  description = "Allowlist scoping the runtime role's bedrock:InvokeModel* IAM policy. Entries are either full ARNs (used verbatim) or bare model IDs (expanded to both the foundation-model ARN and the cross-region inference-profile ARN, region-wildcarded). Empty list = unrestricted (foundation-model/* and inference-profile/* wildcard) -- fine for a sandboxed eval account, but tighten this for a shared one."
  type        = list(string)
  default     = []
}

variable "opensearch_collection_name" {
  description = "Name of the OpenSearch Serverless collection holding the product catalog. Defaults to resource_label."
  type        = string
  default     = ""
}

variable "opensearch_index_name" {
  description = "Index name inside the collection. Must match CATALOG_OPENSEARCH_INDEX passed to the runtime and the --index-name used by scripts/ingest_catalog.py."
  type        = string
  default     = "products"
}

variable "allow_public_access" {
  description = "AOSS network policy: true = public internet access to the collection (simplest for a standalone eval environment, since there's no VPC to peer with). Set false and provide VPC endpoints yourself (edit opensearch.tf) if this needs to run somewhere network-restricted."
  type        = bool
  default     = true
}

variable "additional_read_principals" {
  description = "Extra IAM principal ARNs (besides the runtime role and the Terraform caller) granted read access to the OpenSearch collection -- e.g. your own dev-machine role for ad-hoc queries."
  type        = list(string)
  default     = []
}

variable "additional_write_principals" {
  description = "Extra IAM principal ARNs granted write access to the OpenSearch collection -- e.g. a CI role that runs scripts/ingest_catalog.py."
  type        = list(string)
  default     = []
}
