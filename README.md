# AgentCore Runtime Eval Framework

A standalone, product-search-and-discovery-only AgentCore Runtime, an
OpenSearch Serverless catalog of synthetic products (scales to 100,000+;
ships with 70,000), and a full
agent-evaluation harness (`scripts/eval/`) -- all in one small repo so you
can switch agent models, run stress-test suites, and LLM-judge the results,
with none of the cart/customer-profile/memory/voice machinery a full
e-commerce shopping assistant would carry.

The runtime is a fresh, minimal implementation (same AWS SDKs --
`bedrock-agentcore`, `strands-agents` -- same AgentCore Runtime Terraform
resource types, a documented SSE event contract) built specifically for
this standalone evaluation use case, not a fork of any other codebase.

Validated end-to-end against a real deployment: infrastructure applied,
image built and pushed, 100k products ingested, and a full multi-model
comparison run (3 agent models, LLM-judged) completed with 0 inconclusive
results. See "Known limitations" below for two real bugs that surfaced
during that run and were fixed.

## What's here

```
tf/                      Terraform: ECR repo, OpenSearch Serverless collection,
                         IAM role, AgentCore Runtime + endpoint. No Cognito,
                         no VPC, no Gateway -- SigV4/IAM auth only.
runtime/                 The agent container.
  app/entrypoint.py        AgentCore entrypoint (BedrockAgentCoreApp)
  app/catalog_search.py    The one tool: lexical OpenSearch search
  app/prompt.py             System prompt (teaches the agent the catalog taxonomy)
  Dockerfile
scripts/
  data_gen/generate_catalog.py   Synthetic catalog generator (no LLM calls)
  ingest_catalog.py              Bulk-loads the catalog into OpenSearch
  build_and_push.sh              Build/push the runtime image to ECR
  eval/                           Eval harness (run_eval.py, stress_cases/, etc.)
data/catalog.jsonl              70,000 generated products (~19MB)
docs/eval-process.md            How the eval harness works, end to end
```

## Architecture decisions (and why)

- **Lexical search only, no vectors.** The OpenSearch collection is a
  `SEARCH`-type AOSS collection (BM25 `multi_match` + keyword/range filters),
  not `VECTORSEARCH`. No embedding model, no k-NN index. The agent is taught
  the catalog's segment/category/color taxonomy directly in its system
  prompt (`runtime/app/prompt.py`) and is expected to translate natural
  language into structured filters itself -- that's what actually gets
  evaluated.
- **One tool, no Gateway.** `search_catalog` runs in-process against AOSS
  directly (SigV4, via the runtime's own IAM role) -- there's no Bedrock
  AgentCore Gateway, no MCP. Simpler infrastructure, and it's the only tool
  a search/discovery-only agent needs.
- **No Cognito, no VPC.** The AgentCore Runtime has no `authorizer_configuration`,
  so it's invoked with plain SigV4/IAM auth -- the same mechanism
  `scripts/eval/run_eval.py --runtime-id` already supports. Network mode is
  `PUBLIC`. Fine for a sandboxed eval account; tighten both if you're running
  this somewhere shared.
- **In-memory conversation history.** Session state lives in a plain Python
  dict inside the running container, keyed by `session_id`. AgentCore pins a
  warm microVM per session, so a multi-turn eval case keeps working as long
  as that microVM stays warm. A cold restart loses history -- there's no
  S3/DynamoDB session store here (out of scope for an eval-only tool).
- **`agent.invoke_async()`, not hand-parsed streaming.** The entrypoint
  drives the Strands agent with a single blocking call per turn and reads the
  result from the documented `AgentResult`/`agent.messages` API, rather than
  trying to reconstruct Strands' internal event-stream shapes. The eval
  harness doesn't need token-level streaming -- it reassembles a whole turn
  from however many SSE events arrive -- so this trades a (here, irrelevant)
  streaming nicety for a much more stable implementation.
- **`<product sku="..."/>` / `<suggested_replies>[...]` tags.** The model
  marks which products to show and what quick-reply options to offer inline
  in its answer text; the entrypoint parses those tags into the
  `{"product": {...}}` / `{"suggested_replies": [...]}` content blocks
  `run_eval.py` already knows how to read.

## Catalog taxonomy

`scripts/data_gen/generate_catalog.py` and `runtime/app/prompt.py` must agree
on this -- the agent can only filter on segments/categories/colors it's been
told exist, and the ported `scripts/eval/stress_cases/*.yaml` are grounded
against this exact taxonomy (reverse-engineered from the comments in those
files, which were authored against a similarly-generated catalog).

| Segment | Categories |
|---|---|
| mens | shirts, tshirts, pants, jackets, sweaters, shoes |
| womens | shirts, tshirts, pants, dresses, skirts, jackets, sweaters, shoes |
| kids | shirts, pants, jackets, shoes (no tshirts/dresses/skirts/sweaters) |
| accessories | belts, jewelry (no gender) |

15 colors: black, white, gray, navy, brown, tan, beige, cream, charcoal,
green, red, blue, pink, purple, burgundy.

Materials (cotton, leather, wool, silk, suede, ...) appear in descriptions
only -- there's no structured material field, deliberately, so some stress
cases exercise "the model says it can't confirm an attribute it can't
verify" honesty rather than exact-match filtering.

## Setup

### 1. Deploy the infrastructure

```bash
cd tf
cp terraform.tfvars.example terraform.tfvars   # edit as needed
terraform init
terraform apply
```

This creates the ECR repo and OpenSearch collection, and creates the
AgentCore Runtime pointed at `<ecr_repo>:latest` -- which doesn't exist yet,
so the runtime will be in a non-working state until you push an image (next
step). That's expected; `terraform apply` doesn't fail because of it.

### 2. Build and push the runtime image

```bash
scripts/build_and_push.sh
```

Requires Docker with `buildx` (for `linux/arm64`, which AgentCore Runtime
requires regardless of your host architecture). On macOS with Colima instead
of Docker Desktop, make sure the VM is running first (`colima start`) --
`docker buildx build` fails with a plain "Cannot connect to the Docker
daemon" otherwise.

### 3. Generate and load the catalog

A 70,000-product catalog is already generated at `data/catalog.jsonl`
(~19MB). It's capped at 70k rather than the originally-requested 100k
(~27MB) because GitHub's web upload UI rejects individual files over 25MB --
if you're not distributing this repo through that upload flow (plain `git
push`, git-lfs, or any other transfer), there's no reason to stay under it.
Regenerate at any size with:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r scripts/requirements.txt
python3 scripts/data_gen/generate_catalog.py --count 100000   # or any --count/--seed
```

Then load it into the collection Terraform created:

```bash
python3 scripts/ingest_catalog.py \
  --endpoint "$(terraform -chdir=tf output -raw opensearch_collection_endpoint)" \
  --index "$(terraform -chdir=tf output -raw opensearch_index_name)" \
  --region "$(terraform -chdir=tf output -raw aws_region)"
```

Your AWS credentials need to be a principal AOSS's data-access policy allows
to write -- by default that's whoever ran `terraform apply` (see
`additional_write_principals` in `tf/variables.tf` if that's a different
identity than the one running this script).

### 4. Run the eval suite

```bash
python3 scripts/eval/run_eval.py \
  --runtime-id "$(terraform -chdir=tf output -raw agent_runtime_id)" \
  --account-id "$(terraform -chdir=tf output -raw aws_account_id)" \
  --region "$(terraform -chdir=tf output -raw aws_region)" \
  --test-dir scripts/eval/stress_cases \
  --judge-all --judge-model us.anthropic.claude-sonnet-4-5-20250929-v1:0
```

Or compare several models at once:

```bash
RUNTIME_ID=$(terraform -chdir=tf output -raw agent_runtime_id) \
  scripts/eval/stress_comparison.sh
```

Reports land in `eval_reports/` inside the repo (markdown + a JSON artifact
with the same stem) unless you pass `--output`/an `OUT_DIR` argument --
`eval_reports/` is gitignored, so runs accumulate there without needing to be
committed.

See `scripts/eval/run_eval.py --help` and the comments at the top of
`judge_bakeoff.sh` / `stress_comparison.sh` for the full flag/env-var surface
(model aliases, rounds/pass-rate gating, parallelism, timeouts), and
**[docs/eval-process.md](docs/eval-process.md) for a full explanation of how
the eval process works** -- test case format, execution flow, how the LLM
judge is invoked and scored, and how per-criterion results roll up into a
suite-level verdict.

### 5. Tear down

```bash
cd tf && terraform destroy
```

The OpenSearch Serverless collection bills for a minimum baseline OCU
capacity even sitting completely idle -- don't leave this stack up longer
than you're actively evaluating against it.

### Switching models at eval time

With `allow_config_overrides = true` (the default), `--agent-model` /
`--compare-models` work by sending `config_overrides.agent_model_id` in the
request body -- same mechanism as the source repo. The runtime's IAM role
only has `bedrock:InvokeModel*` on whatever `allowed_bedrock_models` in
`tf/variables.tf` resolves to (empty = unrestricted), so if you scope that
list down, make sure it covers every model you intend to test with.

## Known limitations

- Lexical search only -- no semantic/vector search, so a query with zero
  shared vocabulary with the catalog (title/description/category/brand) will
  return nothing even if conceptually related products exist. The system
  prompt's taxonomy teaching is what makes most natural-language queries work
  despite this (e.g. "loafers" -> `categories=["mens","shoes"]` even though
  "loafer" never appears in the catalog text).
- One tool, one system prompt -- no cart, no customer accounts, no memory
  across sessions beyond the in-process dict, no voice, no multi-brand
  config. This is deliberately scoped to search/discovery only.
- No authentication in front of the runtime beyond IAM/SigV4. Don't point
  `allow_public_access = true` at a catalog you don't want publicly
  queryable, and don't deploy this into a shared/production AWS account
  without tightening `allowed_bedrock_models`, `allow_public_access`, and the
  OpenSearch access-policy principals first.
- **Judge token budget is model-dependent.** `JUDGE_MAX_TOKENS` in
  `scripts/eval/run_eval.py` is 3072 (raised from the ported default of
  1024). Some judge models reason much more verbosely before writing their
  `record_verdict` tool call than others -- at 1024, Claude Sonnet 5
  routinely got truncated mid-tool-call, losing the trailing `score`/
  `verdict` fields and surfacing as `STATUS_ERROR` ("unparseable judge
  response ... got None") on a large fraction of cases. If you pick a judge
  model and see a lot of "inconclusive" results, suspect this first --
  replay one case's judge prompt directly against Bedrock Converse and check
  `stopReason`/`usage.outputTokens` against the cap before assuming the
  agent (not the judge) is at fault.
- **Agent-model run-to-run variance.** Observed directly: an isolated
  single-case smoke test against `us.openai.gpt-5.6-sol` returned an empty
  response (no tool call, zero tokens) twice in a row, yet a full 32-case
  run minutes later scored 31/32. Treat any single `--rounds 1` run as a
  sample, not a verdict, for a model you haven't characterized yet -- use
  `--rounds`/`--pass-rate` (see `run_eval.py --help`) for a more reliable
  read before concluding a model is broken vs. just occasionally flaky.
