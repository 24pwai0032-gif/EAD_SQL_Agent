"""Evaluation: execution accuracy + chart appropriateness.

Execution accuracy compares the RESULT SETS (gold SQL vs what the agent
returned), float tolerance 0.01 — never SQL string similarity, because many
correct queries exist for one question.

This number is the only instrument we have: the agent loop stops when the
model stops calling tools, NOT when the answer is correct. A query that
runs cleanly and answers the wrong question looks identical to a right one.

Usage:
    python eval/run_eval.py               # full eval (needs a working model)
    python eval/run_eval.py --check-gold  # only verify gold SQL executes (no LLM)
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.database import run_query

GOLD_PATH = Path(__file__).resolve().parent / "gold.json"
TOL = 0.01


def _norm_cell(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    return v


def _rows_match(gold_rows, got_rows) -> bool:
    if len(gold_rows) != len(got_rows):
        return False
    if not gold_rows:
        return True
    if len(gold_rows[0]) != len(got_rows[0]):
        return False

    def sort_key(row):
        return tuple(str(c) for c in row)

    for g, a in zip(sorted(gold_rows, key=sort_key), sorted(got_rows, key=sort_key)):
        for gc, ac in zip(g, a):
            gc, ac = _norm_cell(gc), _norm_cell(ac)
            if isinstance(gc, float) and isinstance(ac, float):
                if abs(gc - ac) > TOL:
                    return False
            elif gc != ac:
                return False
    return True


def result_sets_match(gold_cols, gold_rows, got_cols, got_rows) -> bool:
    """Order-insensitive multiset comparison with float tolerance.

    The agent may return extra columns (e.g. it kept the grouping key AND a
    label); if exact shape fails, retry against every same-width column
    subset the agent returned, preferring name matches.
    """
    if _rows_match(gold_rows, got_rows):
        return True
    if not gold_rows or not got_rows or len(got_cols) <= len(gold_cols):
        return False
    # Project the agent's columns down to those matching gold column count,
    # trying name-based selection first, then numeric-only projection.
    name_idx = [i for i, c in enumerate(got_cols) if c in gold_cols]
    candidates = []
    if len(name_idx) == len(gold_cols):
        candidates.append(name_idx)
    if len(gold_cols) == 1:
        for i in range(len(got_cols)):
            candidates.append([i])
    for idx in candidates:
        projected = [[r[i] for i in idx] for r in got_rows]
        if _rows_match(gold_rows, projected):
            return True
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-gold", action="store_true",
                        help="only execute the gold SQL to verify it runs (no LLM)")
    args = parser.parse_args()

    gold = json.loads(GOLD_PATH.read_text())
    print(f"Loaded {len(gold)} gold cases.")

    if args.check_gold:
        bad = 0
        for i, case in enumerate(gold):
            try:
                _, cols, rows = run_query(case["sql"])
                print(f"  [{i:02d}] OK   {len(rows):3d} rows  {case['question'][:60]}")
            except Exception as e:
                bad += 1
                print(f"  [{i:02d}] FAIL {case['question'][:60]} -> {e}")
        sys.exit(1 if bad else 0)

    from agent.graph import ask
    from agent.llm import verify_tool_calling
    verify_tool_calling()

    exec_ok = 0
    chart_ok = 0
    failures = []
    for i, case in enumerate(gold):
        _, gold_cols, gold_rows = run_query(case["sql"])
        try:
            result = ask(case["question"], thread_id=f"eval-{i}")
        except Exception as e:
            failures.append((i, case["question"], f"agent error: {e}"))
            continue

        matched = result_sets_match(gold_cols, gold_rows,
                                    result["columns"], result["rows"])
        exec_ok += matched
        charted = result["chart"] is not None
        chart_right = charted == case["expects_chart"]
        chart_ok += chart_right

        flag = "OK " if matched else "MISS"
        cflag = "chart-ok" if chart_right else f"chart-WRONG(got={charted}, want={case['expects_chart']})"
        print(f"  [{i:02d}] {flag} {cflag}  {case['question'][:60]}")
        if not matched:
            failures.append((i, case["question"], "result set mismatch"))

    n = len(gold)
    print("\n" + "=" * 60)
    print(f"Execution accuracy:    {exec_ok}/{n} = {100.0 * exec_ok / n:.1f}%")
    print(f"Chart appropriateness: {chart_ok}/{n} = {100.0 * chart_ok / n:.1f}%")
    if failures:
        print("\nFailures:")
        for i, q, why in failures:
            print(f"  [{i:02d}] {q[:70]} -> {why}")


if __name__ == "__main__":
    main()
