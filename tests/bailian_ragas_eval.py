"""Score cached Alibaba Cloud Model Studio answers with five RAGAS metrics.

Run ``tests/bailian_eval.py --include-answer`` first. This script checkpoints
each metric per question and emits a combined retrieval + RAGAS report.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from tests.bailian_eval import load_credentials, score_row

REF_FILE = ROOT / "tests" / "test_cases_with_ref.json"
PIPELINE_CACHE_FILE = ROOT / "tests" / "bailian_retrieval_cache.json"
SCORE_CACHE_FILE = ROOT / "tests" / "bailian_ragas_scores.json"
REPORTS_DIR = ROOT / "tests" / "reports"
DEFAULT_CREDENTIALS_FILE = ROOT / "默认业务空间-apiKey-.csv"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_BAILIAN_MODEL = "qwen-plus"

METRIC_NAMES = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
    "answer_correctness",
)
CONTEXT_METRIC_NAMES = ("context_precision", "context_recall")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def row_fingerprint(row: dict, case: dict) -> str:
    payload = {
        "question": case["question"],
        "reference": case["reference"],
        "answer": row["answer"],
        "contexts": [result.get("text", "") for result in row["results"]],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def prepare_rows(cases: list[dict], pipeline_cache_file: Path) -> list[dict]:
    cached = {
        row["id"]: row
        for row in load_json(pipeline_cache_file, [])
        if row.get("id")
    }
    missing: list[str] = []
    rows: list[dict] = []
    for case in cases:
        row = cached.get(case["id"])
        if (
            not row
            or row.get("question") != case["question"]
            or row.get("answer_complete") is not True
            or not str(row.get("answer", "")).strip()
        ):
            missing.append(case["id"])
            continue
        rows.append({
            **row,
            "reference": case["reference"],
            "reference_contexts": case.get("reference_contexts", []),
            "fingerprint": row_fingerprint(row, case),
        })

    if missing:
        preview = " ".join(missing[:20])
        suffix = " ..." if len(missing) > 20 else ""
        raise RuntimeError(
            f"{len(missing)}题缺少与当前题库匹配的完整百炼答案：{preview}{suffix}\n"
            f"答案缓存：{pipeline_cache_file}"
        )
    return rows


def build_evaluators(
    credentials_csv: Path,
    judge_provider: str,
    judge_model: str,
) -> dict[str, Any]:
    from openai import AsyncOpenAI
    from ragas.embeddings.huggingface_provider import HuggingFaceEmbeddings
    from ragas.evaluation import llm_factory
    from ragas.metrics.collections import (
        AnswerCorrectness,
        AnswerRelevancy,
        ContextPrecision,
        ContextRecall,
        Faithfulness,
    )
    from config import EMBED_MODEL

    if judge_provider == "deepseek":
        from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL

        api_key = DEEPSEEK_API_KEY
        base_url = DEEPSEEK_BASE_URL
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY未设置")
    else:
        api_key, base_url, _ = load_credentials(credentials_csv)
        if not base_url:
            raise RuntimeError("百炼judge需要CSV中的openAiCompatible地址")
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=180.0,
        max_retries=1,
    )
    llm = llm_factory(judge_model, client=client)
    embeddings = HuggingFaceEmbeddings(model=EMBED_MODEL)
    return {
        "faithfulness": Faithfulness(llm=llm),
        "answer_relevancy": AnswerRelevancy(llm=llm, embeddings=embeddings),
        "context_precision": ContextPrecision(llm=llm),
        "context_recall": ContextRecall(llm=llm),
        "answer_correctness": AnswerCorrectness(llm=llm, embeddings=embeddings),
    }


def metric_calls(evaluators: dict[str, Any], row: dict) -> dict[str, Callable[[], Coroutine]]:
    question = row["question"]
    answer = row["answer"]
    contexts = [result.get("text", "") for result in row["results"]]
    reference = row["reference"]
    return {
        "faithfulness": lambda: evaluators["faithfulness"].ascore(
            question, answer, contexts
        ),
        "answer_relevancy": lambda: evaluators["answer_relevancy"].ascore(
            question, answer
        ),
        "context_precision": lambda: evaluators["context_precision"].ascore(
            question, reference, contexts
        ),
        "context_recall": lambda: evaluators["context_recall"].ascore(
            question, contexts, reference
        ),
        "answer_correctness": lambda: evaluators["answer_correctness"].ascore(
            question, answer, reference
        ),
    }


async def score_metric(
    name: str,
    call: Callable[[], Coroutine],
    max_retries: int,
) -> tuple[str, float | None, str | None]:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            result = await asyncio.wait_for(call(), timeout=300)
            value = float(result.value) if hasattr(result, "value") else float(result)
            return name, value, None
        except Exception as exc:
            last_error = exc
            if attempt + 1 < max_retries:
                await asyncio.sleep(5)
    return name, None, str(last_error)


def save_score_cache(cache: dict[str, dict], score_cache_file: Path) -> None:
    ordered = sorted(cache.values(), key=lambda row: row["id"])
    atomic_write_json(score_cache_file, ordered)


async def score_rows(
    rows: list[dict],
    refresh: bool,
    max_retries: int,
    workers: int,
    credentials_csv: Path,
    judge_provider: str,
    judge_model: str,
    score_cache_file: Path,
    reuse_context_score_cache: Path | None,
    reuse_context_pipeline_cache: Path | None,
) -> dict[str, dict]:
    cache = {
        row["id"]: row
        for row in load_json(score_cache_file, [])
        if row.get("id")
    }
    reused_scores = {
        row["id"]: row
        for row in load_json(reuse_context_score_cache, [])
        if row.get("id")
    } if reuse_context_score_cache else {}
    reused_pipeline = {
        row["id"]: row
        for row in load_json(reuse_context_pipeline_cache, [])
        if row.get("id")
    } if reuse_context_pipeline_cache else {}

    pending = []
    reused_count = 0
    for row in rows:
        cached = cache.get(row["id"], {})
        if (
            refresh
            or cached.get("fingerprint") != row["fingerprint"]
            or cached.get("judge_provider") != judge_provider
            or cached.get("judge_model") != judge_model
        ):
            cached = {
                "id": row["id"],
                "fingerprint": row["fingerprint"],
                "judge_provider": judge_provider,
                "judge_model": judge_model,
                "scores": {},
                "errors": {},
            }
            cache[row["id"]] = cached

        old_score = reused_scores.get(row["id"])
        old_row = reused_pipeline.get(row["id"])
        same_contexts = old_row and (
            old_row.get("question") == row["question"]
            and [result.get("text", "") for result in old_row.get("results", [])]
            == [result.get("text", "") for result in row.get("results", [])]
        )
        same_judge = old_score and (
            old_score.get("judge_provider") == judge_provider
            and old_score.get("judge_model") == judge_model
        )
        if same_contexts and same_judge:
            copied = False
            for name in CONTEXT_METRIC_NAMES:
                value = old_score.get("scores", {}).get(name)
                if cached.get("scores", {}).get(name) is None and value is not None:
                    cached.setdefault("scores", {})[name] = value
                    cached.setdefault("reused_scores", {})[name] = str(
                        reuse_context_score_cache
                    )
                    copied = True
            reused_count += int(copied)

        missing = [name for name in METRIC_NAMES if cached.get("scores", {}).get(name) is None]
        if missing:
            pending.append((row, missing))

    if reused_count:
        save_score_cache(cache, score_cache_file)
        print(f"复用上下文指标：{reused_count}题（检索切片与评审模型完全一致）")
    print(f"RAGAS待评分：{len(pending)}题，已完成：{len(rows)-len(pending)}题")
    if not pending:
        return cache
    evaluators = build_evaluators(credentials_csv, judge_provider, judge_model)
    semaphore = asyncio.Semaphore(workers)

    async def score_one(index: int, row: dict, missing: list[str]) -> None:
        async with semaphore:
            print(
                f"  [{index}/{len(pending)}] {row['id']} "
                f"({','.join(missing)})...",
                flush=True,
            )
            calls = metric_calls(evaluators, row)
            results = await asyncio.gather(*(
                score_metric(name, calls[name], max_retries) for name in missing
            ))
            entry = cache[row["id"]]
            for name, value, error in results:
                entry.setdefault("scores", {})[name] = value
                if error:
                    entry.setdefault("errors", {})[name] = error
                else:
                    entry.setdefault("errors", {}).pop(name, None)
            entry["fingerprint"] = row["fingerprint"]
            save_score_cache(cache, score_cache_file)

            values = entry["scores"]
            shown = "  ".join(
                f"{name[:4]}={values[name]:.3f}"
                if values.get(name) is not None
                else f"{name[:4]}=ERR"
                for name in METRIC_NAMES
            )
            print(f"  [{index}/{len(pending)}] {row['id']} done: {shown}", flush=True)

    await asyncio.gather(*(
        score_one(index, row, missing)
        for index, (row, missing) in enumerate(pending, 1)
    ))
    return cache


def save_combined_report(
    rows: list[dict],
    score_cache: dict[str, dict],
    elapsed: float,
    judge_provider: str,
    judge_model: str,
    report_prefix: str,
    mode_label: str,
) -> Path:
    retrieval_rows = [score_row(row) for row in rows]
    n = len(rows)
    retrieval_summary = {
        "hit_at_1": round(sum(row["hit_at_1"] for row in retrieval_rows) / n, 4),
        "hit_at_3": round(sum(row["hit_at_3"] for row in retrieval_rows) / n, 4),
        "hit_at_5": round(sum(row["hit_at_5"] for row in retrieval_rows) / n, 4),
        "mrr": round(sum(row["mrr"] for row in retrieval_rows) / n, 4),
    }
    ragas_summary = {}
    valid_counts = {}
    for name in METRIC_NAMES:
        values = [
            score_cache[row["id"]].get("scores", {}).get(name)
            for row in rows
        ]
        values = [value for value in values if value is not None]
        valid_counts[name] = len(values)
        ragas_summary[name] = round(sum(values) / len(values), 4) if values else None

    per_question = []
    for row, retrieval in zip(rows, retrieval_rows):
        ragas = score_cache[row["id"]].get("scores", {})
        per_question.append({
            "id": row["id"],
            "retrieval": retrieval,
            "ragas": {name: ragas.get(name) for name in METRIC_NAMES},
        })

    report = {
        "timestamp": datetime.now().isoformat(),
        "mode": mode_label,
        "answer_generation": {
            "models": sorted({
                str(row.get("answer_model") or row.get("model") or "unknown")
                for row in rows
            }),
            "modes": sorted({
                str(row.get("generation_mode") or row.get("mode") or "unknown")
                for row in rows
            }),
        },
        "ragas_judge": {
            "provider": judge_provider,
            "model": judge_model,
        },
        "n": n,
        "elapsed_s": round(elapsed, 1),
        "retrieval_summary": retrieval_summary,
        "ragas_summary": ragas_summary,
        "ragas_valid_counts": valid_counts,
        "per_question": per_question,
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_prefix = Path(report_prefix).name
    path = REPORTS_DIR / f"{safe_prefix}_{timestamp}.json"
    atomic_write_json(path, report)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="百炼知识问答RAGAS五指标评测")
    parser.add_argument("--ids", nargs="+", help="只评测指定题号")
    parser.add_argument("--refresh", action="store_true", help="重算所选题目的五项指标")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--workers", type=int, default=1, help="并发评分题数")
    parser.add_argument(
        "--judge-provider",
        choices=("deepseek", "bailian"),
        default="deepseek",
    )
    parser.add_argument("--judge-model", default="")
    parser.add_argument(
        "--credentials-csv",
        type=Path,
        default=DEFAULT_CREDENTIALS_FILE,
    )
    parser.add_argument(
        "--pipeline-cache",
        type=Path,
        default=PIPELINE_CACHE_FILE,
        help="包含百炼切片与完整答案的缓存",
    )
    parser.add_argument(
        "--score-cache",
        type=Path,
        default=SCORE_CACHE_FILE,
        help="逐题RAGAS评分缓存",
    )
    parser.add_argument(
        "--reuse-context-score-cache",
        type=Path,
        help="复用同一检索结果已有的context_precision/context_recall评分",
    )
    parser.add_argument(
        "--reuse-context-pipeline-cache",
        type=Path,
        help="与复用评分对应的检索缓存，用于逐题校验切片完全一致",
    )
    parser.add_argument(
        "--report-prefix",
        default="bailian_combined_report",
    )
    parser.add_argument(
        "--mode-label",
        default="bailian_knowledge_chat_agent",
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers必须大于等于1")
    if bool(args.reuse_context_score_cache) != bool(args.reuse_context_pipeline_cache):
        parser.error("复用上下文指标时必须同时提供两个reuse参数")

    cases: list[dict] = load_json(REF_FILE, [])
    if args.ids:
        requested = set(args.ids)
        cases = [case for case in cases if case["id"] in requested]
    if not cases:
        parser.error("没有可评测题目")

    judge_model = args.judge_model or (
        DEFAULT_DEEPSEEK_MODEL
        if args.judge_provider == "deepseek"
        else DEFAULT_BAILIAN_MODEL
    )
    rows = prepare_rows(cases, args.pipeline_cache)
    started = time.time()
    score_cache = asyncio.run(score_rows(
        rows,
        args.refresh,
        args.max_retries,
        args.workers,
        args.credentials_csv,
        args.judge_provider,
        judge_model,
        args.score_cache,
        args.reuse_context_score_cache,
        args.reuse_context_pipeline_cache,
    ))
    path = save_combined_report(
        rows,
        score_cache,
        time.time() - started,
        args.judge_provider,
        judge_model,
        args.report_prefix,
        args.mode_label,
    )
    report = load_json(path, {})

    print("\n检索指标：")
    for name, value in report["retrieval_summary"].items():
        print(f"  {name:24s}: {value:.4f}")
    print("RAGAS指标：")
    for name, value in report["ragas_summary"].items():
        count = report["ragas_valid_counts"][name]
        shown = f"{value:.4f}" if value is not None else "N/A"
        print(f"  {name:24s}: {shown} ({count}/{len(rows)})")
    print(f"报告：{path}")


if __name__ == "__main__":
    main()
