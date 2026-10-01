#!/usr/bin/env python3
"""In-process eval for the catalog search tool (no HTTP/agent involved).

Calls runtime/app/catalog_search.py's search_catalog() directly against the
real OpenSearch index -- answers "is the search backend returning the right
products," not "does the whole conversation behave correctly" (that's
run_eval.py's job).

Test cases live in a YAML file, e.g. scripts/eval/search_cases.yaml:

    - name: "black belt"
      query: "black leather belt"
      categories: [accessories, belts]
      colors: [black]
      max_results: 6
      max_price: 100
      expect_title_contains: ["belt"]
      expect_all_category: "belts"

Usage:
    python scripts/eval/catalog_search_eval.py \\
        --cases scripts/eval/search_cases.yaml \\
        --endpoint https://xxxxx.us-west-2.aoss.amazonaws.com \\
        --index products --region us-west-2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runtime"))


def _coerce_field(item: dict, field: str) -> str:
    val = item.get(field)
    if val is None:
        return ""
    if isinstance(val, list):
        return " ".join(str(v) for v in val)
    return str(val)


def _evaluate_case(case: dict, results: list[dict]) -> list[str]:
    failures: list[str] = []

    if not results:
        if case.get("expect_empty"):
            return failures
        failures.append("no results returned")
        return failures

    for expected in case.get("expect_title_contains", []):
        found = any(expected.lower() in _coerce_field(p, "title").lower() for p in results)
        if not found:
            failures.append(f"no title contains {expected!r}")

    top_expected = case.get("expect_top_title_contains")
    if top_expected and top_expected.lower() not in _coerce_field(results[0], "title").lower():
        failures.append(f"top result {results[0].get('title', '')!r} does not contain {top_expected!r}")

    cat_expected = case.get("expect_all_category")
    if cat_expected:
        wrong = [r for r in results if cat_expected.lower() not in _coerce_field(r, "category").lower()]
        if wrong:
            failures.append(f"{len(wrong)}/{len(results)} have category != {cat_expected!r}")

    if case.get("max_price") is not None:
        over = [r for r in results if r.get("price") is not None and r["price"] > case["max_price"]]
        if over:
            failures.append(f"{len(over)}/{len(results)} exceed max_price={case['max_price']}")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--endpoint", required=True, help="AOSS collection endpoint")
    parser.add_argument("--index", default="products")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    os.environ["OPENSEARCH_ENDPOINT"] = args.endpoint
    os.environ["OPENSEARCH_INDEX"] = args.index
    os.environ["AWS_REGION"] = args.region

    from app.catalog_search import search_catalog

    cases = yaml.safe_load(args.cases.read_text()) or []
    if not isinstance(cases, list):
        print(f"expected YAML list at top level, got {type(cases).__name__}", file=sys.stderr)
        return 2

    print(f"index: {args.index}  cases: {len(cases)} from {args.cases}")
    print("=" * 80)

    passed = failed = 0
    out_records: list[dict] = []

    for case in cases:
        name = case.get("name") or case.get("query", "<unnamed>")
        try:
            results = search_catalog(
                query=case["query"],
                categories=case.get("categories"),
                colors=case.get("colors"),
                min_price=case.get("min_price"),
                max_price=case.get("max_price"),
                max_results=case.get("max_results", 6),
            )
        except Exception as e:
            failed += 1
            print(f"  ERROR  {name}: {type(e).__name__}: {e}")
            out_records.append({"name": name, "error": str(e)})
            continue

        failures = _evaluate_case(case, results)
        if failures:
            failed += 1
            print(f"  FAIL   {name}")
            for f in failures:
                print(f"         - {f}")
        else:
            passed += 1
            print(f"  PASS   {name}  ({len(results)} results)")

        if args.verbose:
            for r in results[:3]:
                print(f"           -> {r.get('title', '')[:60]} | {r.get('category')} | ${r.get('price')}")

        out_records.append({
            "name": name,
            "query": case.get("query"),
            "result_count": len(results),
            "result_titles": [r.get("title", "")[:80] for r in results[:10]],
            "failures": failures,
        })

    print("=" * 80)
    total = passed + failed
    print(f"passed={passed}/{total}  failed={failed}")

    if args.json_out:
        args.json_out.write_text(json.dumps({"cases": out_records, "passed": passed, "failed": failed}, indent=2))
        print(f"wrote per-case results to {args.json_out}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
