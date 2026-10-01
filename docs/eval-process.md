# How the eval framework works

This document explains `scripts/eval/` end to end: what each file does, how a
test case is structured, how a turn gets scored, and how results roll up
into a pass/fail verdict. It assumes you've read the top-level `README.md`
for how to deploy and run things -- this is about *how the eval process
itself works*, not how to invoke it.

## The two evaluators

There are two independent tools, answering two different questions.

- **`run_eval.py`** -- the main harness. Sends real HTTP requests to the
  *deployed* AgentCore Runtime (the same container `scripts/build_and_push.sh`
  built) and scores the responses with deterministic checks and/or an LLM
  judge. Answers: "does the whole agent, end to end, behave correctly?"
- **`catalog_search_eval.py`** -- a narrower, separate tool. Calls
  `runtime/app/catalog_search.py`'s `search_catalog()` function directly,
  in-process, against the real OpenSearch index -- no HTTP, no agent, no
  LLM involved at all. Answers: "is the search backend returning the right
  products for this filter combination?" Its own test-case file is
  `scripts/eval/search_cases.yaml` (a different, simpler schema than
  `run_eval.py`'s -- see that file for examples).

A supporting tool, **`compare_judges.py`**, takes several `run_eval.py` JSON
artifacts (typically the same transcripts, re-scored by different judge
models) and reports inter-judge agreement, score distributions, and
self-preference bias. It's standalone -- it never calls the agent or a judge
itself, only post-processes existing artifacts.

Three shell scripts (`judge_bakeoff.sh`, `stress_comparison.sh`,
`full_eval_pipeline.sh`) orchestrate `run_eval.py` across models and judges;
see the comment block at the top of each for its flags and environment
variables.

## Test case format (`scripts/eval/stress_cases/*.yaml`)

Each file is one test case. `load_test_cases_from_dir`/`load_test_cases_from_s3`
glob `*.yaml`/`*.yml` and skip files starting with `_`.

| Field | Meaning |
|---|---|
| `id` | Test case identifier (defaults to the filename) |
| `description` | Human-readable summary, shown in reports |
| `tags` | Free-form labels (e.g. `[category, color]`) for your own filtering/grouping |
| `queries` | List of strings, one per turn. A single entry is a one-shot test; multiple entries script a multi-turn conversation in the same session |
| `auto_continue` | Whether to keep going past the scripted `queries` if the last turn offered suggested replies instead of products (default `true`, unless the case expects zero products) |
| `max_auto_turns` | Cap on auto-continue turns (default 3) |
| `criteria` | List of assertions (see below) |

Each `criteria` entry:

- `type` -- one of `min_product_count`, `max_product_count`,
  `all_products_under_price`, `all_products_over_price`,
  `all_products_match_category` (+ optional `any_of`),
  `all_products_match_color`, `text_contains`, `text_not_contains`,
  `llm_judge`.
- `value` -- the threshold/expected value for that type.
- `turn` -- which turn to check (negative = from the end; default `-1`, the
  last turn).
- `severity` -- `fail` (gates the case) or `warn` (recorded but doesn't fail it).
- `prompt` -- only for `llm_judge`: the free-text rubric for that specific case.

The 32 cases under `stress_cases/` are grounded against this repo's own
synthetic catalog taxonomy (see `scripts/data_gen/generate_catalog.py`'s
`SEGMENT_CATEGORIES`/`COLORS`) -- e.g. `category_kids_pants.yaml` asserts
`kids`+`pants` exists because the generator puts it there, and
`search_kids_shirts.yaml` deliberately avoids "t-shirts for kids" because the
generator never creates that combination. If you regenerate the catalog with
a different taxonomy, these cases may stop making sense.

### `auto_continue`: why it exists

Some cases intentionally start vague (`"I need a gift"`,
`"I need something to wear"`) to check that the agent asks a clarifying
question rather than guessing. If the agent's last turn had no products but
offered `suggested_replies`, `run_single_round` auto-answers with the first
suggested reply and keeps going (up to `max_auto_turns`) -- simulating a user
who answers the clarifying question. This is disabled by default for cases
that *expect* zero products (the `safety_*.yaml` cases), since there,
getting no products back and stopping is the point.

## Execution flow

1. `call_assistant()` POSTs `{"prompt": ..., "session_id": ..., "config_overrides": {...}}`
   to the runtime's `/invocations` endpoint with `Accept: text/event-stream`,
   signed with SigV4 using your local AWS credentials (there's no Cognito or
   API Gateway in front of this runtime -- see the main README's
   "Architecture decisions" -- so this is the *only* auth path; the
   `--identity-pool-id`/Cognito-token code path in `run_eval.py` is dead code
   here, inherited from a version of the harness built for a different,
   Cognito-fronted deployment).
2. It parses the streamed `data: <json>` lines, pulling out `product`,
   `text`, `suggested_replies`, `toolUse`, and `toolResult` content blocks
   (see `runtime/app/entrypoint.py`'s module docstring for exactly how the
   runtime constructs these), plus a final `metrics` block
   (`cycleCount`/`inputTokens`/`outputTokens`/cache token counts), into a
   `TurnResult`.
3. `run_single_round` sends each scripted `queries[]` entry as a turn in the
   same `session_id` (so multi-turn cases share conversation history inside
   the runtime's in-memory session store), then runs auto-continue turns if
   applicable.
4. **Timeouts/errors never become false failures.** A turn that times out or
   gets an HTTP error returns a `TurnResult` with `error` set instead of an
   empty result; every criterion for that round is then marked `STATUS_ERROR`
   ("not evaluated: turn N timeout...") rather than scored as a failure. A
   slow or unreachable agent is *inconclusive*, never a false "0 products" FAIL.

## Judging

**Mechanism** (`run_llm_judge`):

1. `build_transcript` renders the full conversation so far (every turn, tool
   calls with arguments, tool results, products, text), marking which turn
   is under evaluation, with size caps so one long transcript can't blow the
   judge's context (`JUDGE_MAX_TEXT_CHARS`, `JUDGE_MAX_TOOL_INPUT_CHARS`,
   `JUDGE_MAX_TOOL_RESULT_CHARS`, `JUDGE_MAX_PRODUCTS`).
2. `build_judge_prompt` wraps it with a fixed rubric: a 1-5 integer score,
   PASS at `JUDGE_PASS_THRESHOLD` (3) or above, explicit anti-bias rules (no
   credit for politeness or length, don't invent facts not in the
   transcript), and reasoning written *before* the verdict.
3. The judge is asked to call a `record_verdict` tool
   (`reasoning`/`evidence`/`score`/`verdict`) rather than free-text, so the
   result is already-parsed JSON with no quoting ambiguity. `_call_judge`
   adapts automatically per judge model: it tries forcing the tool call
   (`toolChoice`), falls back to merely offering the tool if the model
   rejects forced tool choice, and falls back further to parsing JSON out of
   plain text if the model rejects tool use outright. It also drops the
   `temperature` parameter for models that reject it (observed: Claude
   Sonnet 5). Each fallback is cached per model so it's paid once per run,
   not once per call.

**`--judge-all`** (`build_default_judge_criterion`): synthesizes a generic
3-part rubric (products-vs-request, text-vs-displayed-products consistency,
appropriateness for vague/unsafe/off-topic requests) for any case that
doesn't already have a hand-authored `llm_judge` criterion. All 32 shipped
stress cases get this treatment when you pass `--judge-all`; a handful also
have a hand-written `llm_judge` criterion of their own for a nuance the
generic rubric can't express (e.g. `multi_turn_shoes_then_price.yaml`'s "did
it honestly handle a turn-2 price filter on turn-1's results" check).

**A note on `JUDGE_MAX_TOKENS`:** this is set to 3072, not the harness's
original default of 1024. Some judge models reason considerably more
verbosely before writing their `record_verdict` call than others; at 1024,
a verbose judge can run out of budget mid-tool-call and lose the trailing
`score`/`verdict` fields, which surfaces as `STATUS_ERROR` ("unparseable
judge response ... got None") rather than a real verdict. If you see a high
inconclusive rate with a new judge model, suspect this first -- it's a token
budget problem, not necessarily an agent problem. (Confirmed directly: a
real judge prompt replayed against Bedrock Converse came back at 978/1024
output tokens, right at the old cap.)

**Retry/backoff:** `_converse_with_retry` retries only *classified-transient*
errors (throttling, 5xx, timeouts, connection errors) with exponential
backoff and jitter, up to a fixed attempt count and a wall-clock budget, then
surfaces as `STATUS_ERROR` rather than a fabricated FAIL. A judge call that
genuinely fails never counts against the agent.

## Scoring & aggregation

Everything uses a three-valued status: `STATUS_PASS` / `STATUS_FAIL` /
`STATUS_ERROR` -- infrastructure/judge problems are always kept distinct
from real agent failures.

- **Per-criterion** (`evaluate_criterion`): pass/fail/error, plus
  `score`/`reasoning`/`evidence` for `llm_judge` criteria.
- **Per-round** (`derive_round_status`): any `fail`-severity criterion in
  `STATUS_FAIL` -> round FAIL. Else any criterion in `STATUS_ERROR` -> round
  ERROR. Else PASS. `warn`-severity criteria never fail a round.
- **Per-case** ("multi-round gating"): each case runs `--rounds` times in
  fresh sessions. `required_passes = ceil(rounds x pass_rate)` (default
  `--pass-rate 0.5`). The case is marked PASS if enough rounds passed, ERROR
  if the errored rounds could plausibly have supplied the missing passes
  (so flaky infrastructure doesn't masquerade as a model failure), else
  FAIL. `pass^k` ("all_rounds_passed") is the stricter signal -- every single
  round passed, not just enough of them -- useful for distinguishing "mostly
  reliable" from "always reliable."
- **Suite-level** (`summarize_results`): totals, `case_pass_rate`,
  `mean_round_pass_rate`, `pass_k_rate`, `mean_judge_score`, and a breakdown
  of failed turns by error reason.

**Why rounds matter in practice:** this repo's own validation run showed it.
A single isolated smoke test against one agent model returned an empty
response twice in a row, but a full 32-case run of the same model minutes
later scored 31/32. A `--rounds 1` run of a model you haven't characterized
yet is a sample, not a verdict -- `--rounds`/`--pass-rate` exist specifically
to average out that kind of run-to-run variance.

## Output

Every `run_eval.py` invocation writes a markdown report and a JSON artifact
with the same stem (the JSON path is the markdown path with `.json`
substituted), both under `eval_reports/` by default (gitignored -- see
`DEFAULT_REPORTS_DIR` near the top of `run_eval.py`), overridable with
`--output`.

- **Markdown**: a summary table (cases passed/failed/inconclusive, mean
  round-pass-rate, pass^k, mean judge score, latency), a per-case table, then
  a detailed per-round/per-turn/per-criterion section with judge reasoning
  and verbatim evidence quotes.
- **JSON artifact**: `schema_version`, full run metadata (agent/judge model,
  rounds/pass-rate, test source), a `summary` block, and every round's turns
  (query, products, text, tool calls/results, tokens, latency) and every
  criterion's verdict. This is the input to `--rejudge-from` (re-score a
  prior run's artifact with a different judge, no new agent calls) and to
  `compare_judges.py`.

## What's inherited vs. what's specific to this repo

This harness is a close port of a larger shopping-assistant project's eval
tooling, adapted for a runtime with no Cognito/API Gateway/Gateway-proxied
tools in front of it. Two things worth knowing if you're reading the source
expecting it to all apply here:

- `--prompt-version` still exists as a CLI flag and still gets forwarded as
  `config_overrides.prompt_version`, but this repo's runtime only has one
  system prompt (see `runtime/app/prompt.py`) and silently ignores the key.
  It's a no-op here, kept only because removing the flag outright would be
  more disruptive than harmless.
- `--config-dir` resolves a runtime URL from `agent_runtime_id`/
  `aws_account_id`/`aws_region` in `terraform output` (this repo's actual
  outputs), not from an `eval_runtime_url`/Cognito-identity-pool shape a
  different deployment topology would produce.
