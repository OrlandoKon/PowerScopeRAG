"""Evaluate DeepSeek direct answering without retrieval or knowledge context.

The benchmark uses the same questions and references as the RAG evaluation,
but sends only the question to the answer model. Answers and RAGAS scores are
checkpointed after every question so interrupted runs can resume safely.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine

from openai import AsyncOpenAI, OpenAI

warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import (  # noqa: E402
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    EMBED_MODEL,
)

REF_FILE = ROOT / "tests" / "test_cases_with_ref.json"
ANSWER_CACHE = ROOT / "tests" / "direct_answer_cache.json"
SCORE_CACHE = ROOT / "tests" / "direct_score_cache.json"
REPORTS_DIR = ROOT / "tests" / "reports"

TEMPERATURE = 0.0
MAX_TOKENS = 1024
SYSTEM_PROMPT = """你是一个专业的电力系统分析助手，专注于400V低压配电网。

请直接基于你自身已有的知识回答用户问题。不要假装看到了未提供的标准、论文或知识库内容。
涉及标准条款、具体数值或研究结论时应准确作答；不能确定时明确说明，不得编造。
回答应简洁、完整，使用中文。"""


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_ordered(data: dict[str, dict], cases: list[dict], path: Path) -> None:
    """Atomically checkpoint rows in benchmark order."""
    ordered = [data[case["id"]] for case in cases if case["id"] in data]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def run_direct_answers(cases: list[dict]) -> list[dict]:
    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        timeout=90.0,
    )
    current_ids = {case["id"] for case in cases}
    existing = {
        row["id"]: row
        for row in load_json(ANSWER_CACHE, [])
        if row.get("id") in current_ids and not row.get("error")
    }
    todo = [case for case in cases if case["id"] not in existing]
    print(f"直接问答待处理：{len(todo)}题，已缓存：{len(existing)}题")

    for case in todo:
        started = time.time()
        print(f"  [{len(existing)+1}/{len(cases)}] {case['id']}...", end=" ", flush=True)
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": case["question"]},
                    ],
                    temperature=TEMPERATURE,
                    max_tokens=MAX_TOKENS,
                )
                answer = (response.choices[0].message.content or "").strip()
                usage = response.usage.model_dump() if response.usage else {}
                existing[case["id"]] = {
                    "id": case["id"],
                    "user_input": case["question"],
                    "response": answer,
                    "reference": case["reference"],
                    "model": DEEPSEEK_MODEL,
                    "temperature": TEMPERATURE,
                    "usage": usage,
                }
                save_ordered(existing, cases, ANSWER_CACHE)
                print(f"完成，{time.time()-started:.1f}s")
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    print(f"重试{attempt+1}...", end=" ", flush=True)
                    time.sleep(5)
        if last_error is not None:
            print(f"失败：{last_error}")

    return [existing[case["id"]] for case in cases if case["id"] in existing]


def build_evaluators():
    from ragas.embeddings.huggingface_provider import HuggingFaceEmbeddings
    from ragas.evaluation import llm_factory
    from ragas.metrics.collections import AnswerCorrectness, AnswerRelevancy

    async_client = AsyncOpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        timeout=120.0,
    )
    evaluator_llm = llm_factory(DEEPSEEK_MODEL, client=async_client)
    embeddings = HuggingFaceEmbeddings(model=EMBED_MODEL)
    return {
        "answer_relevancy": AnswerRelevancy(
            llm=evaluator_llm,
            embeddings=embeddings,
        ),
        "answer_correctness": AnswerCorrectness(
            llm=evaluator_llm,
            embeddings=embeddings,
        ),
    }


async def retry_metric(
    call: Callable[[], Coroutine[Any, Any, Any]],
    attempts: int = 3,
) -> float | None:
    for attempt in range(attempts):
        try:
            result = await call()
            return float(result.value) if hasattr(result, "value") else float(result)
        except Exception as exc:
            if attempt == attempts - 1:
                print(f"失败：{exc}", end=" ")
                return None
            await asyncio.sleep(5)
    return None


async def score_answers_async(rows: list[dict], cases: list[dict]) -> list[dict]:
    evaluators = build_evaluators()
    current_ids = {row["id"] for row in rows}
    existing = {
        row["id"]: row
        for row in load_json(SCORE_CACHE, [])
        if row.get("id") in current_ids
        and row.get("answer_relevancy") is not None
        and row.get("answer_correctness") is not None
    }
    todo = [row for row in rows if row["id"] not in existing]
    print(f"RAGAS待评分：{len(todo)}题，已缓存：{len(existing)}题")

    for row in todo:
        started = time.time()
        print(f"  [{len(existing)+1}/{len(rows)}] {row['id']}...", end=" ", flush=True)
        relevancy, correctness = await asyncio.gather(
            retry_metric(lambda: evaluators["answer_relevancy"].ascore(
                row["user_input"], row["response"],
            )),
            retry_metric(lambda: evaluators["answer_correctness"].ascore(
                row["user_input"], row["response"], row["reference"],
            )),
        )
        existing[row["id"]] = {
            "id": row["id"],
            "answer_relevancy": relevancy,
            "answer_correctness": correctness,
        }
        save_ordered(existing, cases, SCORE_CACHE)
        print(
            f"relevancy={relevancy if relevancy is not None else 'N/A'} "
            f"correctness={correctness if correctness is not None else 'N/A'} "
            f"耗时={time.time()-started:.1f}s"
        )

    return [existing[row["id"]] for row in rows if row["id"] in existing]


def save_report(rows: list[dict], scores: list[dict], elapsed: float) -> Path:
    metric_names = ("answer_relevancy", "answer_correctness")
    summary = {}
    for name in metric_names:
        values = [row[name] for row in scores if row.get(name) is not None]
        summary[name] = round(sum(values) / len(values), 4) if values else None

    usage_totals: dict[str, int] = {}
    for row in rows:
        for key, value in row.get("usage", {}).items():
            if isinstance(value, int):
                usage_totals[key] = usage_totals.get(key, 0) + value

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"ragas_report_direct_{timestamp}.json"
    report = {
        "timestamp": datetime.now().isoformat(),
        "mode": "deepseek_direct_no_rag",
        "n_answers": len(rows),
        "n_scores": len(scores),
        "model": DEEPSEEK_MODEL,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "elapsed_s": round(elapsed, 1),
        "usage": usage_totals,
        "summary": summary,
        "per_question": scores,
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="DeepSeek无知识库直接问答评测")
    parser.add_argument("--ids", nargs="+", help="只评测指定题号")
    stage = parser.add_mutually_exclusive_group()
    stage.add_argument("--answers-only", action="store_true", help="只生成直接问答答案")
    stage.add_argument("--score-only", action="store_true", help="只对已有答案评分")
    args = parser.parse_args()

    if not DEEPSEEK_API_KEY:
        parser.error("DEEPSEEK_API_KEY未配置")
    cases: list[dict] = load_json(REF_FILE, [])
    if args.ids:
        requested = set(args.ids)
        cases = [case for case in cases if case["id"] in requested]
    if not cases:
        parser.error("没有可评测题目")

    started = time.time()
    if args.score_only:
        all_answers = {row["id"]: row for row in load_json(ANSWER_CACHE, [])}
        rows = [all_answers[case["id"]] for case in cases if case["id"] in all_answers]
    else:
        rows = run_direct_answers(cases)
    if args.answers_only:
        print(f"答案阶段完成：{len(rows)}/{len(cases)}")
        return

    scores = asyncio.run(score_answers_async(rows, cases))
    report_path = save_report(rows, scores, time.time() - started)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print("\n汇总结果：")
    for name, value in report["summary"].items():
        print(f"  {name}: {value if value is not None else 'N/A'}")
    print(f"报告：{report_path}")


if __name__ == "__main__":
    main()
