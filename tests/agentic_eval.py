#!/usr/bin/env python3
"""Compare ordinary retrieval with Agentic RAG on complex evidence tasks."""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentic.orchestrator import run_agentic_retrieval  # noqa: E402
from config import DEEPSEEK_MODEL, DEFAULT_RETRIEVAL_MODE  # noqa: E402
from pipeline.retrieve import retrieve  # noqa: E402


CASES_FILE = ROOT / "tests" / "agentic_eval_cases.json"
CACHE_FILE = ROOT / "tests" / "agentic_eval_cache.json"
REPORT_DIR = ROOT / "tests" / "reports"


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def score_case(case: dict, result: dict) -> dict:
    baseline_sources = {
        item.get("source") for item in result.get("baseline", [])
    }
    agentic = result.get("agentic", {})
    agentic_sources = {
        item.get("source") for item in agentic.get("evidence", [])
    }
    actual_tools = {
        item["task"]["tool"] for item in agentic.get("trace", [])
    }
    required_tools = set(case["required_tools"])
    expected_sources = set(case.get("expected_sources", []))
    missing_text = " ".join(
        agentic.get("missing_data", [])
        + [agentic.get("assessment", "")]
        + [
            item.get("description", "")
            for item in agentic.get("coverage", [])
            if not item.get("satisfied")
        ]
    )

    return {
        "baseline_source_hit": (
            bool(expected_sources & baseline_sources) if expected_sources else None
        ),
        "agentic_source_hit": (
            bool(expected_sources & agentic_sources) if expected_sources else None
        ),
        "tool_recall": (
            len(required_tools & actual_tools) / len(required_tools)
            if required_tools else 1.0
        ),
        "tool_precision": (
            len(required_tools & actual_tools) / len(actual_tools)
            if actual_tools else 0.0
        ),
        "tool_exact_match": required_tools == actual_tools,
        "sufficiency_correct": (
            agentic.get("sufficient") == case["expected_sufficient"]
        ),
        "missing_signal_recall": (
            sum(signal in missing_text for signal in case["expected_missing_signals"])
            / len(case["expected_missing_signals"])
            if case["expected_missing_signals"] else 1.0
        ),
        "rounds": agentic.get("rounds_completed", 0),
        "evidence_count": len(agentic.get("evidence", [])),
        "validation_issue_count": len(agentic.get("validation_issues", [])),
    }


def _average(values: list[float | bool | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return sum(present) / len(present) if present else None


def summarize(cases: list[dict], cache: dict) -> dict:
    rows = []
    for case in cases:
        result = cache.get(case["id"])
        if not result or "error" in result:
            continue
        rows.append(score_case(case, result))
    return {
        "completed": len(rows),
        "total": len(cases),
        "baseline_source_hit": _average([r["baseline_source_hit"] for r in rows]),
        "agentic_source_hit": _average([r["agentic_source_hit"] for r in rows]),
        "tool_recall": _average([r["tool_recall"] for r in rows]),
        "tool_precision": _average([r["tool_precision"] for r in rows]),
        "tool_exact_match": _average([r["tool_exact_match"] for r in rows]),
        "sufficiency_accuracy": _average([r["sufficiency_correct"] for r in rows]),
        "missing_signal_recall": _average([r["missing_signal_recall"] for r in rows]),
        "average_rounds": _average([r["rounds"] for r in rows]),
        "average_evidence_count": _average([r["evidence_count"] for r in rows]),
        "average_validation_issues": _average([
            r["validation_issue_count"] for r in rows
        ]),
    }


def _format_percent(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def write_report(cases: list[dict], cache: dict, summary: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORT_DIR / f"agentic_eval_{timestamp}.md"
    lines = [
        "# Agentic RAG 专项评测报告",
        "",
        f"- 模型：`{DEEPSEEK_MODEL}`",
        f"- 检索模式：`{DEFAULT_RETRIEVAL_MODE}`",
        f"- 完成题数：{summary['completed']}/{summary['total']}",
        "",
        "## 汇总指标",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 普通RAG来源命中率 | {_format_percent(summary['baseline_source_hit'])} |",
        f"| Agentic RAG来源命中率 | {_format_percent(summary['agentic_source_hit'])} |",
        f"| 工具召回率 | {_format_percent(summary['tool_recall'])} |",
        f"| 工具精确率 | {_format_percent(summary['tool_precision'])} |",
        f"| 工具完全匹配率 | {_format_percent(summary['tool_exact_match'])} |",
        f"| 充分性判断准确率 | {_format_percent(summary['sufficiency_accuracy'])} |",
        f"| 缺失信息召回率 | {_format_percent(summary['missing_signal_recall'])} |",
        f"| 平均检索轮数 | {summary['average_rounds'] or 0:.2f} |",
        f"| 平均证据数 | {summary['average_evidence_count'] or 0:.2f} |",
        "",
        "## 逐题结果",
        "",
        "| 题号 | 类型 | 工具召回 | 来源命中 | 充分性判断 | 轮数 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for case in cases:
        result = cache.get(case["id"])
        if not result or "error" in result:
            continue
        score = score_case(case, result)
        source_hit = score["agentic_source_hit"]
        lines.append(
            f"| {case['id']} | {case['category']} | "
            f"{score['tool_recall']:.0%} | "
            f"{'-' if source_hit is None else ('是' if source_hit else '否')} | "
            f"{'是' if score['sufficiency_correct'] else '否'} | "
            f"{score['rounds']} |"
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids", nargs="*", help="只运行指定题号")
    parser.add_argument("--refresh", action="store_true", help="忽略已有缓存")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-follow-up-rounds", type=int, default=1)
    args = parser.parse_args()

    all_cases = _load_json(CASES_FILE, [])
    selected = [
        case for case in all_cases
        if not args.ids or case["id"] in set(args.ids)
    ]
    cache = _load_json(CACHE_FILE, {})
    print(f"Agentic RAG专项评测：{len(selected)}题，模型={DEEPSEEK_MODEL}")
    print("类别：", dict(Counter(case["category"] for case in selected)))

    for index, case in enumerate(selected, 1):
        cached = cache.get(case["id"])
        if (
            not args.refresh
            and cached
            and "error" not in cached
            and cached.get("model") == DEEPSEEK_MODEL
        ):
            continue
        print(f"[{index}/{len(selected)}] {case['id']} {case['query'][:36]}", flush=True)
        try:
            baseline = retrieve(case["query"], top_k=args.top_k)
            agentic = run_agentic_retrieval(
                case["query"],
                max_follow_up_rounds=args.max_follow_up_rounds,
            )
            cache[case["id"]] = {
                "model": DEEPSEEK_MODEL,
                "baseline": baseline,
                "agentic": agentic.model_dump(),
            }
        except Exception as exc:
            cache[case["id"]] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"  ERROR: {cache[case['id']]['error']}")
        _save_json(CACHE_FILE, cache)

    summary = summarize(selected, cache)
    report_path = write_report(selected, cache, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"报告已保存：{report_path}")


if __name__ == "__main__":
    main()
