#!/usr/bin/env python3
"""Repeat AnswerRelevancy on unchanged answers to measure judge variability."""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openai import AsyncOpenAI  # noqa: E402
from ragas.evaluation import llm_factory  # noqa: E402
from ragas.metrics.collections import AnswerRelevancy  # noqa: E402

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL  # noqa: E402
from tests.agentic_unified_eval import _save  # noqa: E402
from tests.ragas_eval import _build_evaluator_embeddings  # noqa: E402

DEFAULT_IDS = (
    "Q035", "Q085", "Q143", "Q148", "Q155", "Q074", "Q081", "Q113",
    "Q167", "Q066", "Q151", "Q181", "Q001", "Q020", "Q040", "Q060",
    "Q080", "Q100", "Q120", "Q140", "Q160", "Q180",
)
REPORT = ROOT / "tests" / "reports" / "answer_relevancy_repeat.json"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids", nargs="+", default=DEFAULT_IDS)
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")

    cases = {row["id"]: row for row in json.loads(
        (ROOT / "tests" / "test_cases_with_ref.json").read_text(encoding="utf-8")
    )}
    retrieval = json.loads((ROOT / "tests" / "agentic_unified_cache.json").read_text(encoding="utf-8"))
    baseline = json.loads((ROOT / "tests" / "agentic_unified_ragas_scores.json").read_text(encoding="utf-8"))
    rows = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
    judge = llm_factory(
        DEEPSEEK_MODEL,
        client=AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL),
        max_tokens=8192,
        extra_body={"thinking": {"type": "disabled"}},
    )
    metric = AnswerRelevancy(llm=judge, embeddings=_build_evaluator_embeddings())

    for index, item_id in enumerate(args.ids, 1):
        if item_id not in cases or item_id not in retrieval or item_id not in baseline:
            parser.error(f"missing case or score: {item_id}")
        question = cases[item_id]["question"]
        answer = retrieval[item_id]["answer"]
        fingerprint = hashlib.sha256(json.dumps(
            [DEEPSEEK_MODEL, question, answer], ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        row = rows.get(item_id, {})
        scores = row.get("repeats", []) if row.get("fingerprint") == fingerprint else []
        while len(scores) < args.repeats:
            result = await metric.ascore(question, answer)
            scores.append(float(result.value))
            rows[item_id] = {
                "fingerprint": fingerprint,
                "stored_score": baseline[item_id]["scores"]["answer_relevancy"],
                "repeats": scores,
            }
            _save(REPORT, rows)
        print(f"[{index}/{len(args.ids)}] {item_id}: stored={row.get('stored_score', baseline[item_id]['scores']['answer_relevancy']):.3f}, repeats={scores[:args.repeats]}", flush=True)

    spreads = [
        max([rows[item_id]["stored_score"], *rows[item_id]["repeats"][:args.repeats]])
        - min([rows[item_id]["stored_score"], *rows[item_id]["repeats"][:args.repeats]])
        for item_id in args.ids
    ]
    print(json.dumps({
        "questions": len(args.ids),
        "mean_score_range": round(sum(spreads) / len(spreads), 4),
        "range_over_0.2": sum(value > 0.2 for value in spreads),
        "report": str(REPORT),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
