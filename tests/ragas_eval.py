"""
tests/ragas_eval.py — RAGAS 全链路评测（检索 + 生成）

对 test_cases_with_ref.json 中每道题执行完整 RAG pipeline，
然后用 RAGAS 评估 5 个维度：

  faithfulness        — 生成答案是否完全基于检索内容（幻觉检测）
  answer_relevancy    — 生成答案是否和问题相关
  context_precision   — 检索到的 chunk 是否都和问题相关（无噪声）
  context_recall      — 检索结果是否覆盖了参考答案所需信息
  answer_correctness  — 生成答案和参考答案的事实一致性

前提：
  1. 先运行 tests/gen_reference.py 生成 test_cases_with_ref.json
  2. 在 .env 中配置 DEEPSEEK_API_KEY

用法:
    python tests/ragas_eval.py               # 全部题目
    python tests/ragas_eval.py --ids Q001 Q005  # 调试
    python tests/ragas_eval.py --mode dense  # 对比不同检索模式
"""

import argparse
import hashlib
import json
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

REF_FILE    = ROOT / "tests" / "test_cases_with_ref.json"
REPORTS_DIR = ROOT / "tests" / "reports"
PIPELINE_REVISION = "hierarchical-grounded-evidence-v14"


def _build_evaluator_llm():
    from openai import AsyncOpenAI
    from ragas.evaluation import llm_factory
    client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    # Faithfulness can produce a long structured claim list on dense answers.
    return llm_factory(DEEPSEEK_MODEL, client=client, max_tokens=4096)


def _build_evaluator_embeddings():
    from ragas.embeddings.huggingface_provider import HuggingFaceEmbeddings
    from config import EMBED_MODEL
    return HuggingFaceEmbeddings(model=EMBED_MODEL)


def run_pipeline(cases: list[dict], mode: str, cache_file: Path) -> list[dict]:
    """对每道题跑完整 retrieve + generate，逐题保存缓存，支持断点续跑。"""
    from pipeline.retrieve import retrieve
    from pipeline.generate import _SYSTEM_PROMPT, rag_answer

    pipeline_fingerprint = hashlib.sha256(
        f"{mode}\n{PIPELINE_REVISION}\n{_SYSTEM_PROMPT}".encode("utf-8")
    ).hexdigest()

    # 加载已有缓存（断点续跑）
    existing: dict[str, dict] = {}
    if cache_file.exists():
        for r in json.loads(cache_file.read_text(encoding="utf-8")):
            existing[r["id"]] = r

    stale_ids = []
    for case in cases:
        cached = existing.get(case["id"])
        if cached and (
            cached.get("user_input") != case["question"]
            or cached.get("reference") != case.get("reference", "")
            or cached.get("reference_contexts") != case.get("reference_contexts", [])
            or cached.get("pipeline_fingerprint") != pipeline_fingerprint
        ):
            stale_ids.append(case["id"])
            existing.pop(case["id"], None)

    todo = [c for c in cases if c["id"] not in existing]
    completed = len(cases) - len(todo)
    print(f"  待处理：{len(todo)} 题 / 已缓存：{completed} 题")
    if stale_ids:
        print(f"  题目已更新，旧缓存失效：{' '.join(stale_ids)}")

    n = len(cases)
    for i, case in enumerate(todo):
        done = completed + i
        print(f"  [{done+1}/{n}] {case['id']} ...", end=" ", flush=True)
        t = time.time()
        for attempt in range(3):
            try:
                hits = retrieve(case["question"], top_k=5, mode=mode)
                answer = rag_answer(case["question"], hits)
                break
            except Exception as e:
                if attempt == 2:
                    print(f"❌ {e}")
                    hits, answer = [], f"[ERROR] {e}"
                    break
                print(f"⚠ retry {attempt+1}...", end=" ", flush=True)
                time.sleep(5)
        elapsed = time.time() - t
        existing[case["id"]] = {
            "id":                  case["id"],
            "user_input":          case["question"],
            "retrieved_contexts":  [h["content"] for h in hits],
            "response":            answer,
            "reference":           case.get("reference", ""),
            "reference_contexts":  case.get("reference_contexts", []),
            "pipeline_fingerprint": pipeline_fingerprint,
        }
        print(f"✅ {elapsed:.1f}s")
        # 每题保存一次
        _save_cache(existing, cache_file)

    return [existing[c["id"]] for c in cases if c["id"] in existing]


def _save_cache(data: dict, path: Path):
    # Keep unselected rows when --ids is used for a targeted refresh.
    ordered = sorted(data.values(), key=lambda row: row["id"])
    path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")


def _row_fingerprint(row: dict) -> str:
    """Identify the exact pipeline output and reference used for scoring."""
    payload = {
        "user_input": row["user_input"],
        "response": row["response"],
        "retrieved_contexts": row["retrieved_contexts"],
        "reference": row["reference"],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _save_score_cache(data: dict, path: Path):
    ordered = {key: data[key] for key in sorted(data)}
    path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")


def ragas_score(
    rows: list[dict],
    evaluator_llm,
    evaluator_embeddings,
    cache_file: Path,
    refresh: bool = False,
) -> tuple[dict, list[dict]]:
    """用 RAGAS collections 指标逐条打分，逐题保存并支持断点续跑。"""
    import asyncio
    from ragas.metrics.collections import (
        Faithfulness, AnswerRelevancy,
        ContextPrecision, ContextRecall, AnswerCorrectness,
    )

    metrics = {
        "faithfulness":       Faithfulness(llm=evaluator_llm),
        "answer_relevancy":   AnswerRelevancy(llm=evaluator_llm, embeddings=evaluator_embeddings),
        "context_precision":  ContextPrecision(llm=evaluator_llm),
        "context_recall":     ContextRecall(llm=evaluator_llm),
        "answer_correctness": AnswerCorrectness(llm=evaluator_llm, embeddings=evaluator_embeddings),
    }

    score_cache: dict[str, dict] = {}
    if cache_file.exists():
        loaded = json.loads(cache_file.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            score_cache = loaded

    metric_names = list(metrics)

    async def _score_row(row: dict, initial: dict | None = None) -> dict:
        m = metrics
        r = row
        scores = {name: (initial or {}).get(name) for name in metric_names}

        for attempt in range(3):
            missing = [name for name in metric_names if scores.get(name) is None]
            if not missing:
                break
            all_calls = {
                "faithfulness": lambda: m["faithfulness"].ascore(
                    r["user_input"], r["response"], r["retrieved_contexts"]),
                "answer_relevancy": lambda: m["answer_relevancy"].ascore(
                    r["user_input"], r["response"]),
                "context_precision": lambda: m["context_precision"].ascore(
                    r["user_input"], r["reference"], r["retrieved_contexts"]),
                "context_recall": lambda: m["context_recall"].ascore(
                    r["user_input"], r["retrieved_contexts"], r["reference"]),
                "answer_correctness": lambda: m["answer_correctness"].ascore(
                    r["user_input"], r["response"], r["reference"]),
            }
            calls = {name: all_calls[name]() for name in missing}
            results = await asyncio.gather(*calls.values(), return_exceptions=True)
            for name, result in zip(calls, results):
                if isinstance(result, Exception):
                    if "insufficient balance" in str(result).lower() or "余额不足" in str(result):
                        raise RuntimeError("余额不足，RAGAS 评测已暂停；已完成分数保存在缓存中") from result
                    print(f"\n  ⚠ {name} 失败 [{r['id']}]（第 {attempt+1} 次）: {result}")
                    continue
                scores[name] = float(result.value) if hasattr(result, "value") else float(result)
            if any(scores.get(name) is None for name in metric_names) and attempt < 2:
                await asyncio.sleep(5)
        return scores

    async def _run_all():
        per_row = []
        n = len(rows)
        for i, row in enumerate(rows):
            print(f"  [{i+1}/{n}] {row['id']}...", end=" ", flush=True)
            fingerprint = _row_fingerprint(row)
            cached = score_cache.get(row["id"], {})
            initial = (
                cached.get("scores", {})
                if not refresh and cached.get("fingerprint") == fingerprint
                else {}
            )
            complete = all(initial.get(name) is not None for name in metric_names)
            scores = initial if complete else await _score_row(row, initial)
            score_cache[row["id"]] = {
                "fingerprint": fingerprint,
                "scores": scores,
            }
            _save_score_cache(score_cache, cache_file)
            per_row.append({"id": row["id"], **scores})
            valid = {k: v for k, v in scores.items() if v is not None}
            prefix = "缓存 " if complete else ""
            print(prefix + "  ".join(f"{k[:4]}={v:.3f}" for k, v in valid.items()))
        return per_row

    per_row = asyncio.run(_run_all())

    summary = {}
    for k in metrics:
        vals = [r[k] for r in per_row if r.get(k) is not None]
        summary[k] = round(sum(vals) / len(vals), 4) if vals else None
    return summary, per_row


def save_report(rows: list[dict], summary: dict, per_row: list[dict], mode: str, elapsed: float):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"ragas_report_{mode.replace('+','_')}_{ts}.json"

    metric_names = list(summary)
    valid_counts = {
        name: sum(row.get(name) is not None for row in per_row)
        for name in metric_names
    }
    report = {
        "timestamp": datetime.now().isoformat(),
        "mode": mode,
        "n": len(rows),
        "elapsed_s": round(elapsed, 1),
        "summary": summary,
        "valid_counts": valid_counts,
        "per_question": [
            {
                "id": r["id"],
                "question": rows[i]["user_input"][:80],
                **{k: v for k, v in r.items() if k != "id"},
            }
            for i, r in enumerate(per_row)
        ],
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(description="RAGAS 全链路评测")
    parser.add_argument("--ids",  nargs="+", metavar="ID", help="只测指定题目")
    parser.add_argument("--mode", default="hybrid+rerank",
                        choices=[
                            "dense", "hybrid", "hybrid+rerank",
                            "dense+doc2query", "hybrid+doc2query",
                            "hybrid+rerank+doc2query",
                            "hybrid+rerank+structured",
                            "hybrid+rerank+hierarchical",
                        ])
    parser.add_argument("--skip-pipeline", action="store_true",
                        help="跳过 RAG pipeline，直接读已缓存的 pipeline_cache.json")
    parser.add_argument("--refresh-scores", action="store_true",
                        help="忽略已有 RAGAS 评分缓存，重新评分")
    args = parser.parse_args()

    if not DEEPSEEK_API_KEY:
        print("错误：DEEPSEEK_API_KEY 未设置，请在 .env 文件中配置")
        sys.exit(1)

    if not REF_FILE.exists():
        print(f"错误：{REF_FILE} 不存在，请先运行 python tests/gen_reference.py")
        sys.exit(1)

    cases: list[dict] = json.loads(REF_FILE.read_text(encoding="utf-8"))
    if args.ids:
        cases = [c for c in cases if c["id"] in args.ids]
    print(f"\n=== RAGAS 评测  |  题目数={len(cases)}  |  mode={args.mode} ===\n")

    # ── Step 1: RAG pipeline ──
    cache_file = ROOT / "tests" / f"pipeline_cache_{args.mode.replace('+','_')}.json"
    if args.skip_pipeline and cache_file.exists():
        rows = json.loads(cache_file.read_text(encoding="utf-8"))
        current_ids = {case["id"] for case in cases}
        rows = [r for r in rows if r["id"] in current_ids]
        print(f"已加载 pipeline 缓存：{len(rows)} 条")
    else:
        print("Step 1/2  运行 RAG pipeline（retrieve + generate）...")
        t0 = time.time()
        rows = run_pipeline(cases, args.mode, cache_file)
        print(f"Pipeline 完成，耗时 {time.time()-t0:.0f}s，已缓存到 {cache_file.name}\n")

    # ── Step 2: RAGAS scoring ──
    print("Step 2/2  RAGAS 打分（需调用 DeepSeek judge LLM）...")
    evaluator_llm = _build_evaluator_llm()
    evaluator_embeddings = _build_evaluator_embeddings()
    score_cache_file = ROOT / "tests" / f"ragas_score_cache_{args.mode.replace('+','_')}.json"
    t0 = time.time()
    summary, per_row = ragas_score(
        rows,
        evaluator_llm,
        evaluator_embeddings,
        score_cache_file,
        refresh=args.refresh_scores,
    )
    elapsed = time.time() - t0

    # ── 打印汇总 ──
    print(f"\n{'='*55}")
    for k, v in summary.items():
        valid = sum(row.get(k) is not None for row in per_row)
        value = f"{v:.4f}" if v is not None else "N/A"
        print(f"  {k:30s}: {value}  ({valid}/{len(rows)})")
    print(f"{'='*55}")
    print(f"  耗时：{elapsed:.0f}s  |  题目：{len(rows)}\n")

    path = save_report(rows, summary, per_row, args.mode, elapsed)
    print(f"报告已保存：{path}")


if __name__ == "__main__":
    main()
