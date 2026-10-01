#!/usr/bin/env python3
"""
Judge bake-off: compare several LLM judges that scored the *same* transcripts.

Feed it the JSON artifacts written by run_eval.py, one per judge (typically one
original run plus `--rejudge-from` re-scorings of it). Artifacts may span several
agent models; verdicts are matched on (agent model, test case, round, criterion).

Usage:
    python scripts/eval/compare_judges.py out/*.json --output out/bakeoff_summary.md

What it reports, and why each matters when picking a judge:
- Inconclusive rate per judge (status=error): a judge that cannot produce a
  valid verdict is unusable as a gate, however smart it is.
- Output mode per judge (tool_use vs text): structured-output compliance.
- Verdict distribution and mean/histogram of 1-5 scores: a judge that gives
  everything 5/5 carries no signal; one that fails everything is miscalibrated.
- Pairwise agreement and Cohen's kappa on PASS/FAIL: kappa corrects for the
  agreement expected by chance when most verdicts are PASS.
- Unanimous rate and the full list of disagreements with each judge's reasoning:
  this is the part a human reads to decide who is *right*.
- Mean score per judge per agent-model family: a judge that scores its own
  family higher than the others do is showing self-preference bias.
"""

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path


def model_family(model_id):
    """Provider segment of a Bedrock model id (mirrors run_eval.model_family)."""
    if not model_id:
        return None
    parts = str(model_id).lower().split(".")
    if len(parts) >= 2 and parts[0] in {"us", "eu", "apac", "global", "jp", "au", "ca", "sa", "me"}:
        parts = parts[1:]
    return parts[0] if parts else None


def _iter_sections(artifact: dict):
    """Yield (agent_model_id, section) for single-model and comparison artifacts."""
    if "models" in artifact:
        for model_id, section in artifact["models"].items():
            yield model_id, section
    else:
        yield artifact.get("agent_model_id"), artifact


def collect_verdicts(artifacts: list) -> dict:
    """Flatten artifacts into {judge_id: {key: verdict}} where
    key = (agent_model_id, case_id, round_index, criterion_index) and
    verdict = {"status", "score", "reasoning", "output_mode", "criterion_type"}.
    Only llm_judge criteria are kept; deterministic ones are identical across judges."""
    by_judge = {}
    for artifact in artifacts:
        judge = artifact.get("judge_model_id") or "unknown-judge"
        verdicts = by_judge.setdefault(judge, {})
        for agent_model, section in _iter_sections(artifact):
            for case in section.get("test_cases", []):
                for rnd in case.get("rounds", []):
                    for idx, cr in enumerate(rnd.get("criteria", [])):
                        if cr.get("criterion_type") != "llm_judge":
                            continue
                        key = (agent_model, case["id"], rnd.get("round_index", 0), idx)
                        verdicts[key] = {
                            "status": cr.get("status"),
                            "score": cr.get("score"),
                            "reasoning": cr.get("reasoning") or "",
                            "output_mode": cr.get("judge_output_mode"),
                        }
    return by_judge


def cohens_kappa(pairs: list) -> float | None:
    """Cohen's kappa for a list of (label_a, label_b) with binary labels."""
    n = len(pairs)
    if n == 0:
        return None
    agree = sum(1 for a, b in pairs if a == b) / n
    labels = {label for pair in pairs for label in pair}
    expected = 0.0
    for label in labels:
        pa = sum(1 for a, _ in pairs if a == label) / n
        pb = sum(1 for _, b in pairs if b == label) / n
        expected += pa * pb
    if expected >= 1.0:
        return 1.0
    return (agree - expected) / (1 - expected)


def compare(artifacts: list) -> dict:
    """Compute the bake-off summary. Pure function; rendering is separate."""
    by_judge = collect_verdicts(artifacts)
    judges = sorted(by_judge)
    common_keys = set.intersection(*(set(v) for v in by_judge.values())) if by_judge else set()

    per_judge = {}
    for judge in judges:
        verdicts = by_judge[judge]
        statuses = Counter(v["status"] for v in verdicts.values())
        scores = [v["score"] for v in verdicts.values() if isinstance(v["score"], (int, float))]
        modes = Counter(v["output_mode"] or "n/a" for v in verdicts.values())
        # Mean score per agent family: the self-preference signal.
        by_family = defaultdict(list)
        for key, v in verdicts.items():
            if isinstance(v["score"], (int, float)):
                by_family[model_family(key[0]) or "unknown"].append(v["score"])
        per_judge[judge] = {
            "judge_family": model_family(judge),
            "total": len(verdicts),
            "pass": statuses.get("pass", 0),
            "fail": statuses.get("fail", 0),
            "error": statuses.get("error", 0),
            "error_rate": statuses.get("error", 0) / len(verdicts) if verdicts else 0.0,
            "output_modes": dict(modes),
            "mean_score": statistics.mean(scores) if scores else None,
            "score_histogram": {s: sum(1 for x in scores if x == s) for s in range(1, 6)},
            "mean_score_by_agent_family": {fam: statistics.mean(v) for fam, v in sorted(by_family.items())},
        }

    # Pairwise agreement on keys where both judges were conclusive.
    pairwise = []
    for a, b in combinations(judges, 2):
        pairs = [
            (by_judge[a][k]["status"], by_judge[b][k]["status"])
            for k in common_keys
            if by_judge[a][k]["status"] in ("pass", "fail") and by_judge[b][k]["status"] in ("pass", "fail")
        ]
        agreement = sum(1 for x, y in pairs if x == y) / len(pairs) if pairs else None
        score_pairs = [
            (by_judge[a][k]["score"], by_judge[b][k]["score"])
            for k in common_keys
            if isinstance(by_judge[a][k]["score"], (int, float)) and isinstance(by_judge[b][k]["score"], (int, float))
        ]
        mean_abs_score_diff = statistics.mean(abs(x - y) for x, y in score_pairs) if score_pairs else None
        pairwise.append({"a": a, "b": b, "n": len(pairs), "agreement": agreement, "kappa": cohens_kappa(pairs), "mean_abs_score_diff": mean_abs_score_diff})

    # Unanimity and disagreements across all judges.
    disagreements = []
    unanimous = 0
    conclusive_keys = 0
    for key in sorted(common_keys, key=lambda k: (str(k[0]), k[1], k[2], k[3])):
        statuses = {j: by_judge[j][key]["status"] for j in judges}
        if any(s not in ("pass", "fail") for s in statuses.values()):
            continue
        conclusive_keys += 1
        if len(set(statuses.values())) == 1:
            unanimous += 1
        else:
            disagreements.append(
                {
                    "agent_model": key[0],
                    "case_id": key[1],
                    "round_index": key[2],
                    "criterion_index": key[3],
                    "verdicts": {j: {"status": by_judge[j][key]["status"], "score": by_judge[j][key]["score"], "reasoning": by_judge[j][key]["reasoning"]} for j in judges},
                }
            )

    return {
        "judges": judges,
        "common_verdicts": len(common_keys),
        "conclusive_common_verdicts": conclusive_keys,
        "unanimous": unanimous,
        "unanimous_rate": unanimous / conclusive_keys if conclusive_keys else None,
        "per_judge": per_judge,
        "pairwise": pairwise,
        "disagreements": disagreements,
    }


def _fmt(value, pct=False, digits=2):
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%" if pct else f"{value:.{digits}f}"


def render_markdown(summary: dict, sources: list) -> str:
    lines = ["# Judge Bake-off", ""]
    lines.append("**Artifacts:** " + ", ".join(f"`{Path(s).name}`" for s in sources))
    lines.append(f"**Judges:** {len(summary['judges'])} | **Verdicts scored by every judge:** {summary['common_verdicts']}")
    lines.append(f"**Unanimous verdicts (all judges conclusive and agree):** {summary['unanimous']}/{summary['conclusive_common_verdicts']} ({_fmt(summary['unanimous_rate'], pct=True)})")
    lines += ["", "## Per judge", "", "| Judge | Family | Verdicts | PASS | FAIL | Inconclusive | Output modes | Mean score | Score histogram 1..5 |", "|---|---|---|---|---|---|---|---|---|"]
    for judge, s in summary["per_judge"].items():
        modes = ", ".join(f"{k}:{v}" for k, v in sorted(s["output_modes"].items()))
        hist = " ".join(str(s["score_histogram"][i]) for i in range(1, 6))
        lines.append(f"| `{judge}` | {s['judge_family']} | {s['total']} | {s['pass']} | {s['fail']} | {s['error']} ({_fmt(s['error_rate'], pct=True)}) | {modes} | {_fmt(s['mean_score'])} | {hist} |")

    families = sorted({fam for s in summary["per_judge"].values() for fam in s["mean_score_by_agent_family"]})
    if families:
        lines += ["", "## Mean score by agent-model family (self-preference check)", "", "| Judge | " + " | ".join(families) + " |", "|---|" + "---|" * len(families)]
        for judge, s in summary["per_judge"].items():
            cells = [_fmt(s["mean_score_by_agent_family"].get(f)) for f in families]
            marker = " ⚠️" if s["judge_family"] in families else ""
            lines.append(f"| `{judge}`{marker} | " + " | ".join(cells) + " |")
        lines.append("")
        lines.append("⚠️ = the judge shares a family with one of the agent models it scored; compare its column for that family against the other judges'.")

    lines += ["", "## Pairwise agreement (PASS/FAIL, both judges conclusive)", "", "| Judge A | Judge B | n | Agreement | Cohen's κ | Mean |Δscore| |", "|---|---|---|---|---|---|"]
    for p in summary["pairwise"]:
        lines.append(f"| `{p['a']}` | `{p['b']}` | {p['n']} | {_fmt(p['agreement'], pct=True)} | {_fmt(p['kappa'])} | {_fmt(p['mean_abs_score_diff'])} |")

    lines += ["", f"## Disagreements ({len(summary['disagreements'])})", ""]
    if not summary["disagreements"]:
        lines.append("_None: every conclusive verdict was unanimous._")
    for d in summary["disagreements"]:
        lines.append(f"### {d['case_id']} — agent `{d['agent_model']}`, round {d['round_index'] + 1}, criterion #{d['criterion_index'] + 1}")
        lines.append("")
        for judge, v in d["verdicts"].items():
            reasoning = v["reasoning"].replace("\n", " ")
            lines.append(f"- `{judge}`: **{str(v['status']).upper()}** (score {v['score']}) — {reasoning[:400]}{'…' if len(reasoning) > 400 else ''}")
        lines.append("")
    lines += ["---", "*Generated by compare_judges.py*"]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Compare LLM judges that scored the same run_eval transcripts")
    parser.add_argument("artifacts", nargs="+", help="run_eval JSON artifacts, one per judge (same transcripts)")
    parser.add_argument("--output", default=None, help="Markdown output path (default: print to stdout)")
    parser.add_argument("--json", default=None, help="Also write the raw summary as JSON to this path")
    args = parser.parse_args()

    artifacts = [json.loads(Path(p).read_text()) for p in args.artifacts]
    summary = compare(artifacts)
    if len(summary["judges"]) < 2:
        print("Need artifacts from at least two different judge models", file=sys.stderr)
        sys.exit(1)
    report = render_markdown(summary, args.artifacts)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(report)
        print(f"Bake-off summary written to {args.output}")
    else:
        print(report)
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
