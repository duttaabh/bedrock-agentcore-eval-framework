#!/bin/zsh
# LLM judge bake-off: several judge models score IDENTICAL transcripts.
#
# Design
#   1. The agent runs once per agent model over a test-case directory, judged
#      by the base judge (--judge-all so every case gets an LLM verdict).
#   2. Those recorded transcripts are re-judged by each other judge from the
#      JSON artifact (run_eval.py --rejudge-from). No agent calls: the three
#      judges see byte-identical conversations, so any difference is a judge
#      difference, not agent non-determinism.
#   3. compare_judges.py turns the artifacts into one summary: inconclusive
#      rate, output mode, score distribution, pairwise agreement / Cohen's
#      kappa, disagreements with each judge's reasoning, and mean score per
#      agent-model family (self-preference check).
#
# Pick agent models from different provider families so the family table has
# something to compare (default: Anthropic haiku vs OpenAI gpt-6-luna).
#
# Usage
#   scripts/eval/judge_bakeoff.sh [OUT_DIR]
#
# Environment overrides (defaults shown)
#   AWS_PROFILE_NAME=default   AWS profile passed to run_eval.py --profile
#   AWS_REGION_NAME=us-west-2
#   RUNTIME_ID=<required, no default>   agent_runtime_id from `terraform output`
#   CASES=<repo>/scripts/eval/stress_cases                  test-case directory
#   AGENTS="haiku gpt-6-luna"                                agent models (aliases or ids)
#   BASE_JUDGE=gpt-5.6-sol                                   judge used on the live run
#   OTHER_JUDGES="gpt-5.6-terra gpt-5.6-luna"                judges applied via re-judge
#   WORKERS=12                   concurrent cases (live runs) / judge calls (re-judges)
#   AGENT_TIMEOUT=300            per-turn read timeout for the live runs (see
#                                stress_comparison.sh for why 300 and not 120)
#
# Output: OUT_DIR/<agent>__<judge>.{md,json} per (agent, judge), logs/, and
# bakeoff_summary.{md,json}. Exit code is compare_judges.py's.
set -u

SCRIPT_DIR="${0:A:h}"
REPO="${SCRIPT_DIR:h:h}"
OUT="${1:-$REPO/eval_reports/judge_bakeoff_$(date '+%Y%m%d_%H%M%S')}"

AWS_PROFILE_NAME="${AWS_PROFILE_NAME:-default}"
AWS_REGION_NAME="${AWS_REGION_NAME:-us-west-2}"
if [[ -z "${RUNTIME_ID:-}" ]]; then
  RUNTIME_ID=$(terraform -chdir="$REPO/tf" output -raw agent_runtime_id 2>/dev/null)
fi
[[ -z "${RUNTIME_ID:-}" ]] && { echo "Set RUNTIME_ID or run this from a repo with tf/ applied."; exit 1; }
CASES="${CASES:-$REPO/scripts/eval/stress_cases}"
AGENTS=(${=AGENTS:-haiku gpt-6-luna})
BASE_JUDGE="${BASE_JUDGE:-gpt-5.6-sol}"
OTHER_JUDGES=(${=OTHER_JUDGES:-gpt-5.6-terra gpt-5.6-luna})
WORKERS="${WORKERS:-12}"
AGENT_TIMEOUT="${AGENT_TIMEOUT:-300}"

# --parallel/--max-workers: cases (and judge calls) run WORKERS at a time.
COMMON=(--profile "$AWS_PROFILE_NAME" --region "$AWS_REGION_NAME" --parallel --max-workers "$WORKERS")

cd "$REPO" || exit 1
mkdir -p "$OUT/logs"
echo "Bake-off output: $OUT"
echo "Agents: ${AGENTS[*]} | base judge: $BASE_JUDGE | other judges: ${OTHER_JUDGES[*]} | cases: $CASES | workers: $WORKERS"
ART=()

for AGENT in "${AGENTS[@]}"; do
  echo "===== [$(date '+%H:%M:%S')] agent=$AGENT : live run, judge=$BASE_JUDGE ====="
  python3 scripts/eval/run_eval.py "${COMMON[@]}" \
    --runtime-id "$RUNTIME_ID" --test-dir "$CASES" \
    --agent-model "$AGENT" \
    --judge-all --judge-model "$BASE_JUDGE" \
    --agent-timeout "$AGENT_TIMEOUT" \
    --output "$OUT/${AGENT}__${BASE_JUDGE}.md" > "$OUT/logs/${AGENT}__${BASE_JUDGE}.log" 2>&1
  echo "  exit=$?"
  ART+=("$OUT/${AGENT}__${BASE_JUDGE}.json")

  for JUDGE in "${OTHER_JUDGES[@]}"; do
    echo "===== [$(date '+%H:%M:%S')] agent=$AGENT : re-judge with $JUDGE ====="
    python3 scripts/eval/run_eval.py "${COMMON[@]}" \
      --rejudge-from "$OUT/${AGENT}__${BASE_JUDGE}.json" \
      --judge-model "$JUDGE" \
      --output "$OUT/${AGENT}__${JUDGE}.md" > "$OUT/logs/${AGENT}__${JUDGE}.log" 2>&1
    echo "  exit=$?"
    ART+=("$OUT/${AGENT}__${JUDGE}.json")
  done
done

echo "===== [$(date '+%H:%M:%S')] comparing judges ====="
uv run python scripts/eval/compare_judges.py "${ART[@]}" \
  --output "$OUT/bakeoff_summary.md" --json "$OUT/bakeoff_summary.json"
STATUS=$?
echo "ALL_DONE $(date '+%H:%M:%S') -> $OUT/bakeoff_summary.md"
exit $STATUS
