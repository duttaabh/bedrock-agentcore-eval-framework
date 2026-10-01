#!/bin/zsh
# Full evaluation pipeline, end to end, in one background-friendly command:
#   1. judge_bakeoff.sh      - three GPT 5.6 judges on identical transcripts
#   2. stress_comparison.sh  - all agent models, LLM judge on every case
#
# The stress comparison runs with the default judge (gpt-5.6-sol) without
# waiting for a human to read the bake-off: its JSON artifacts can be re-scored
# with whichever judge the bake-off favours in a couple of minutes via
#   run_eval.py --rejudge-from <artifact.json> --judge-model <judge> --parallel --max-workers 12
# so nothing is lost if the bake-off picks a different judge.
#
# Usage
#   scripts/eval/full_eval_pipeline.sh [OUT_ROOT]
# Environment: see judge_bakeoff.sh and stress_comparison.sh (both honour the same
# variables, e.g. WORKERS, RUNTIME_ID, AWS_PROFILE_NAME).
set -u
SCRIPT_DIR="${0:A:h}"
REPO="${SCRIPT_DIR:h:h}"
OUT_ROOT="${1:-$REPO/eval_reports/eval_$(date '+%Y%m%d_%H%M%S')}"
mkdir -p "$OUT_ROOT"

echo "===== [$(date '+%H:%M:%S')] STAGE 1/2 judge bake-off -> $OUT_ROOT/bakeoff ====="
"$SCRIPT_DIR/judge_bakeoff.sh" "$OUT_ROOT/bakeoff"
echo "===== [$(date '+%H:%M:%S')] STAGE 1/2 done (exit=$?) ====="

echo "===== [$(date '+%H:%M:%S')] STAGE 2/2 stress comparison -> $OUT_ROOT/stress ====="
"$SCRIPT_DIR/stress_comparison.sh" "$OUT_ROOT/stress"
STATUS=$?
echo "===== [$(date '+%H:%M:%S')] STAGE 2/2 done (exit=$STATUS) ====="
echo "PIPELINE_DONE $OUT_ROOT"
exit $STATUS
