#!/bin/zsh
# Multi-model stress comparison: run the stress suite against N agent models,
# all in parallel (--parallel), with an LLM judge on every case.
#
# One markdown + one JSON artifact for the run. The JSON can later be
# re-scored with a different judge via
# `run_eval.py --rejudge-from <artifact> --judge-model <judge>` without
# re-running the agents.
#
# Usage
#   scripts/eval/stress_comparison.sh [OUT_DIR]
#
# Environment overrides (defaults shown)
#   AWS_PROFILE_NAME=default   AWS profile passed to run_eval.py --profile
#   AWS_REGION_NAME=us-west-2
#   RUNTIME_ID=<required, no default>   agent_runtime_id from `terraform output`
#   CASES=<repo>/scripts/eval/stress_cases                  test-case directory
#   MODELS=gpt-5.6-terra,gpt-5.6-luna,gpt-6-astra,gpt-6-luna,gpt-6-sol,haiku,sonnet-5,opus-5,fable,deepseek-v3.2,kimi-k3,glm-5
#   JUDGE=gpt-5.6-sol            pick via scripts/eval/judge_bakeoff.sh
#   ROUNDS=1  PASS_RATE=0.5      rounds per case and gate (see run_eval.py)
#   WORKERS=<number of models>   total concurrent (model, case) units. run_eval.py
#                                submits case-major, so N workers for N models puts
#                                ~1 in-flight session on each model's quota. Lower it
#                                if the logs show ThrottlingException / HTTP 429.
#   AGENT_TIMEOUT=300            per-turn read timeout (seconds) for the agent's
#                                streamed response. A turn that exceeds it is recorded
#                                as inconclusive (not as "0 products"). 300 rather than
#                                run_eval.py's 120 default because a mixed model set
#                                can include slow ones -- raise it further if logs show
#                                timeouts for a particular model.
#
# Exit code: 0 all passed, 1 any failure, 2 inconclusive only.
set -u

SCRIPT_DIR="${0:A:h}"
REPO="${SCRIPT_DIR:h:h}"
OUT="${1:-$REPO/eval_reports/stress_eval_$(date '+%Y%m%d_%H%M%S')}"

AWS_PROFILE_NAME="${AWS_PROFILE_NAME:-default}"
AWS_REGION_NAME="${AWS_REGION_NAME:-us-west-2}"
if [[ -z "${RUNTIME_ID:-}" ]]; then
  RUNTIME_ID=$(terraform -chdir="$REPO/tf" output -raw agent_runtime_id 2>/dev/null)
fi
[[ -z "${RUNTIME_ID:-}" ]] && { echo "Set RUNTIME_ID or run this from a repo with tf/ applied."; exit 1; }
CASES="${CASES:-$REPO/scripts/eval/stress_cases}"
MODELS="${MODELS:-gpt-5.6-terra,gpt-5.6-luna,gpt-6-astra,gpt-6-luna,gpt-6-sol,haiku,sonnet-5,opus-5,fable,deepseek-v3.2,kimi-k3,glm-5}"
JUDGE="${JUDGE:-gpt-5.6-sol}"
ROUNDS="${ROUNDS:-1}"
PASS_RATE="${PASS_RATE:-0.5}"
# Default: one worker per model (count the comma-separated MODELS list).
MODEL_COUNT=$(( $(echo "$MODELS" | tr -cd ',' | wc -c) + 1 ))
WORKERS="${WORKERS:-$MODEL_COUNT}"
AGENT_TIMEOUT="${AGENT_TIMEOUT:-300}"

cd "$REPO" || exit 1
mkdir -p "$OUT"
echo "Stress comparison output: $OUT"
echo "Models: $MODELS | judge: $JUDGE | rounds: $ROUNDS @ $PASS_RATE | workers: $WORKERS | agent timeout: ${AGENT_TIMEOUT}s"

python3 scripts/eval/run_eval.py \
  --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION_NAME" \
  --runtime-id "$RUNTIME_ID" --test-dir "$CASES" \
  --compare-models "$MODELS" \
  --judge-all --judge-model "$JUDGE" \
  --rounds "$ROUNDS" --pass-rate "$PASS_RATE" \
  --parallel --max-workers "$WORKERS" \
  --agent-timeout "$AGENT_TIMEOUT" \
  --output "$OUT/model_comparison.md" > "$OUT/run.log" 2>&1
STATUS=$?

echo "ALL_DONE $(date '+%H:%M:%S') -> $OUT (exit=$STATUS)"
exit $STATUS
