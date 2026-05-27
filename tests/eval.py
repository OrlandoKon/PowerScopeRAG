"""
tests/eval.py — PowerScopeRAG 检索质量评测

对 tests/test_cases.json 中的每道题调用 pipeline/retrieve.py，
评估期望文档是否出现在 Top-K 检索结果中。

指标：
  Hit@1 / Hit@3 / Hit@5  — Top-K 内命中率
  MRR                    — 平均倒数排名（越高越好，最大1.0）

用法：
    python tests/eval.py               # 评测全部题目
    python tests/eval.py --ids Q01 Q05 # 只测指定题目（调试用）
    python tests/eval.py --top-k 10    # 调整检索返回数量（默认10）
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.retrieve import retrieve

CASES_FILE = ROOT / "tests" / "test_cases.json"
REPORTS_DIR = ROOT / "tests" / "reports"


# ── 指标计算 ──────────────────────────────────────────────────────────────────

def hit_at_k(expected: list[str], hits: list[dict], k: int) -> bool:
    sources = {h["source"] for h in hits[:k]}
    return any(s in sources for s in expected)


def reciprocal_rank(expected: list[str], hits: list[dict]) -> float:
    for rank, h in enumerate(hits, 1):
        if h["source"] in expected:
            return 1.0 / rank
    return 0.0


def eval_case(case: dict, top_k: int) -> dict:
    hits = retrieve(case["question"], top_k=top_k)
    return {
        "hit_at_1":  hit_at_k(case["expected_sources"], hits, 1),
        "hit_at_3":  hit_at_k(case["expected_sources"], hits, 3),
        "hit_at_5":  hit_at_k(case["expected_sources"], hits, 5),
        "mrr":       reciprocal_rank(case["expected_sources"], hits),
        "retrieved":  [h["source"] for h in hits[:5]],
        "distances":  [round(h["distance"], 4) for h in hits[:5]],
    }


# ── 报告生成 ──────────────────────────────────────────────────────────────────

def _bar(value: float, width: int = 20) -> str:
    filled = round(value * width)
    return "█" * filled + "░" * (width - filled)


def _icon(v: bool) -> str:
    return "✅" if v else "❌"


def generate_report(cases: list[dict], results: dict, top_k: int, elapsed: float) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n = len(cases)
    h1  = sum(results[c["id"]]["hit_at_1"] for c in cases) / n
    h3  = sum(results[c["id"]]["hit_at_3"] for c in cases) / n
    h5  = sum(results[c["id"]]["hit_at_5"] for c in cases) / n
    mrr = sum(results[c["id"]]["mrr"]      for c in cases) / n

    lines = [
        "# PowerScopeRAG 检索评测报告",
        "",
        f"**生成时间**：{ts}  |  **题目数**：{n}  |  **top_k**：{top_k}  |  **耗时**：{elapsed:.1f}s",
        "",
        "## 汇总指标",
        "",
        "| 指标 | 值 | 进度条 |",
        "|------|----|--------|",
        f"| Hit@1 | {h1:.1%} | {_bar(h1)} |",
        f"| Hit@3 | {h3:.1%} | {_bar(h3)} |",
        f"| Hit@5 | {h5:.1%} | {_bar(h5)} |",
        f"| MRR   | {mrr:.3f} | {_bar(mrr)} |",
        "",
        "## 逐题结果",
        "",
        "| 题号 | 类别 | Hit@1 | Hit@3 | Hit@5 | MRR | Top-1 命中文档 |",
        "|------|------|-------|-------|-------|-----|--------------|",
    ]

    for c in cases:
        r = results[c["id"]]
        top1 = r["retrieved"][0] if r["retrieved"] else "—"
        top1_short = (top1[:40] + "…") if len(top1) > 40 else top1
        lines.append(
            f"| {c['id']} | {c['category']} "
            f"| {_icon(r['hit_at_1'])} | {_icon(r['hit_at_3'])} | {_icon(r['hit_at_5'])} "
            f"| {r['mrr']:.2f} | {top1_short} |"
        )

    # 失败题目详情
    failed = [c for c in cases if not results[c["id"]]["hit_at_5"]]
    if failed:
        lines += ["", "## 未命中题目（Hit@5=❌）", ""]
        for c in failed:
            r = results[c["id"]]
            lines += [
                f"### {c['id']}",
                f"**问题**：{c['question']}",
                f"**期望文档**：{', '.join(c['expected_sources'])}",
                f"**实际Top-5**：",
            ]
            for i, (src, dist) in enumerate(zip(r["retrieved"], r["distances"]), 1):
                lines.append(f"  {i}. `{src}`  dist={dist}")
            lines.append("")

    return "\n".join(lines)


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PowerScopeRAG 检索质量评测")
    parser.add_argument("--ids", nargs="+", metavar="ID",
                        help="只评测指定题目，如 Q01 Q05（不填则评测全部）")
    parser.add_argument("--top-k", type=int, default=10,
                        help="检索返回数量（默认10）")
    args = parser.parse_args()

    cases: list[dict] = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    if args.ids:
        cases = [c for c in cases if c["id"] in args.ids]
        if not cases:
            print(f"未找到指定题目：{args.ids}")
            sys.exit(1)

    print(f"\nPowerScopeRAG 检索评测  |  题目数={len(cases)}  |  top_k={args.top_k}")
    print("=" * 60)

    t0 = time.time()
    results: dict = {}

    for case in cases:
        r = eval_case(case, top_k=args.top_k)
        results[case["id"]] = r
        print(
            f"  {case['id']:4s}  Hit@1={_icon(r['hit_at_1'])}  Hit@5={_icon(r['hit_at_5'])}"
            f"  MRR={r['mrr']:.2f}  →  {r['retrieved'][0][:50] if r['retrieved'] else '—'}"
        )

    elapsed = time.time() - t0
    n = len(cases)
    h1  = sum(results[c["id"]]["hit_at_1"] for c in cases) / n
    h3  = sum(results[c["id"]]["hit_at_3"] for c in cases) / n
    h5  = sum(results[c["id"]]["hit_at_5"] for c in cases) / n
    mrr = sum(results[c["id"]]["mrr"]      for c in cases) / n

    print(f"\n{'='*60}")
    print(f"  Hit@1={h1:.1%}  Hit@3={h3:.1%}  Hit@5={h5:.1%}  MRR={mrr:.3f}  ({n}题, {elapsed:.1f}s)")

    report = generate_report(cases, results, args.top_k, elapsed)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"report_{ts}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"  报告已保存：{report_path}")


if __name__ == "__main__":
    main()
