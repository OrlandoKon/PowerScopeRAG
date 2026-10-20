#!/usr/bin/env python3
"""Evaluate the unified /rag workflow without reusing transitional-arm caches."""

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentic.assembly import (  # noqa: E402
    assemble_planned_answer_context,
    refine_answer_context,
)
from config import (  # noqa: E402
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEFAULT_RETRIEVAL_MODE,
)
from tests.eval import reciprocal_rank  # noqa: E402
from tests.ragas_eval import _build_evaluator_embeddings, ragas_score  # noqa: E402

CASES_FILE = ROOT / "tests" / "test_cases_with_ref.json"
CACHE_FILE = ROOT / "tests" / "agentic_unified_cache.json"
SCORE_FILE = ROOT / "tests" / "agentic_unified_ragas_scores.json"
REPORTS_DIR = ROOT / "tests" / "reports"
SOURCE_FILES = [
    ROOT / "api.py",
    ROOT / "pipeline" / "retrieve.py",
    ROOT / "pipeline" / "generate.py",
    *sorted((ROOT / "agentic").glob("*.py")),
]


def _fingerprint() -> str:
    digest = hashlib.sha256(
        f"{DEEPSEEK_MODEL}|{DEFAULT_RETRIEVAL_MODE}|unified-top5".encode()
    )
    for path in SOURCE_FILES:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _save(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    temporary.replace(path)


def _answer_context_signature(hits: list[dict] | None) -> list[tuple]:
    return [
        tuple(hit.get(key) for key in (
            "source", "content", "context_format", "H1", "H2", "H3",
        ))
        for hit in hits or []
    ]


def _answer_fingerprint(question: str, hits: list[dict]) -> str:
    digest = hashlib.sha256((ROOT / "pipeline" / "generate.py").read_bytes())
    digest.update(json.dumps(
        [DEEPSEEK_MODEL, question, _answer_context_signature(hits)],
        ensure_ascii=False,
    ).encode("utf-8"))
    return digest.hexdigest()


def _summary(cases: list[dict], cache: dict, fingerprint: str) -> dict:
    pairs = [
        (case, cache[case["id"]]["hits"])
        for case in cases
        if cache.get(case["id"], {}).get("fingerprint") == fingerprint
        and cache[case["id"]].get("hits") is not None
    ]
    n = len(pairs)
    if not n:
        return {"completed": 0, "total": len(cases)}
    ranks = [
        reciprocal_rank(case["expected_sources"], hits)
        for case, hits in pairs
    ]
    return {
        "completed": n,
        "total": len(cases),
        "hit_at_1": sum(rank == 1 for rank in ranks) / n,
        "hit_at_3": sum(rank >= 1 / 3 for rank in ranks) / n,
        "hit_at_5": sum(rank >= 1 / 5 for rank in ranks) / n,
        "mrr": sum(ranks) / n,
        "misses": [
            case["id"] for (case, _), rank in zip(pairs, ranks) if rank == 0
        ],
    }


def _retrieve(
    cases: list[dict], cache: dict, base_url: str, fingerprint: str,
    refresh: bool = False,
) -> None:
    timeout = httpx.Timeout(180.0, connect=10.0)
    with httpx.Client(base_url=base_url, timeout=timeout, trust_env=False) as client:
        for index, case in enumerate(cases, 1):
            row = cache.get(case["id"], {})
            if (not refresh and row.get("fingerprint") == fingerprint
                    and row.get("question") == case["question"]
                    and row.get("hits") is not None):
                continue
            print(f"[{index}/{len(cases)}] {case['id']} retrieval", flush=True)
            try:
                payload = {
                    "query": case["question"],
                    "max_follow_up_rounds": 1,
                    "knowledge_top_k": 5,
                    "case_top_k": 3,
                    "include_trace": True,
                    "generate_answer": False,
                }
                for attempt in range(3):
                    try:
                        response = client.post("/rag", json=payload)
                        response.raise_for_status()
                        break
                    except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                        retryable = (
                            isinstance(exc, httpx.TransportError)
                            or exc.response.status_code in {502, 503, 504}
                        )
                        if not retryable or attempt == 2:
                            raise
                        time.sleep(2 * (attempt + 1))
                result = response.json()
                hits = assemble_planned_answer_context(case["question"], result)
                hits = refine_answer_context(
                    case["question"], hits, allow_search=False,
                )
                updated = {
                    "fingerprint": fingerprint,
                    "question": case["question"],
                    "hits": hits,
                    "result": result,
                }
                if (row.get("question") == case["question"]
                        and _answer_context_signature(row.get("hits"))
                        == _answer_context_signature(hits)
                        and row.get("answer")):
                    updated["answer"] = row["answer"]
                    if row.get("answer_fingerprint"):
                        updated["answer_fingerprint"] = row["answer_fingerprint"]
                cache[case["id"]] = updated
            except Exception as exc:
                cache[case["id"]] = {
                    "fingerprint": fingerprint,
                    "question": case["question"],
                    "error": f"{type(exc).__name__}: {exc}",
                }
                _save(CACHE_FILE, cache)
                raise
            _save(CACHE_FILE, cache)


def _reassemble(cases: list[dict], cache: dict, fingerprint: str) -> None:
    """Rebuild contexts from saved tool traces without paid retrieval calls."""
    for case in cases:
        row = cache.get(case["id"], {})
        if row.get("question") != case["question"] or not row.get("result"):
            raise RuntimeError(f"{case['id']} 缺少可重组的取证轨迹")
        hits = assemble_planned_answer_context(case["question"], row["result"])
        updated_hits = refine_answer_context(
            case["question"], hits, allow_search=False,
        )
        if (_answer_context_signature(updated_hits)
                != _answer_context_signature(row.get("hits"))):
            row.pop("answer", None)
            row.pop("answer_fingerprint", None)
        row["hits"] = updated_hits
        row["fingerprint"] = fingerprint
        _save(CACHE_FILE, cache)


def _generate(cases: list[dict], cache: dict, fingerprint: str) -> None:
    from pipeline.generate import rag_answer, review_answer

    for index, case in enumerate(cases, 1):
        row = cache.get(case["id"], {})
        if row.get("fingerprint") != fingerprint or row.get("hits") is None:
            raise RuntimeError(f"{case['id']} 尚无当前版本的检索结果")
        answer_fingerprint = _answer_fingerprint(case["question"], row["hits"])
        if row.get("answer") and row.get("answer_fingerprint") == answer_fingerprint:
            continue
        print(f"[{index}/{len(cases)}] {case['id']} answer", flush=True)
        draft = rag_answer(case["question"], row["hits"])
        row["answer"] = review_answer(case["question"], row["hits"], draft)
        row["answer_fingerprint"] = answer_fingerprint
        _save(CACHE_FILE, cache)


def _ragas(cases: list[dict], cache: dict, fingerprint: str) -> dict:
    from openai import AsyncOpenAI
    from ragas.evaluation import llm_factory

    rows = []
    for case in cases:
        row = cache.get(case["id"], {})
        if (row.get("fingerprint") != fingerprint or not row.get("answer")
                or row.get("answer_fingerprint") != _answer_fingerprint(
                    case["question"], row["hits"],
                )):
            raise RuntimeError(f"{case['id']} 尚无当前版本的答案")
        rows.append({
            "id": case["id"],
            "user_input": case["question"],
            "retrieved_contexts": [hit["content"] for hit in row["hits"]],
            "response": row["answer"],
            "reference": case["reference"],
        })
    evaluator = llm_factory(
        DEEPSEEK_MODEL,
        client=AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL),
        max_tokens=8192,
        extra_body={"thinking": {"type": "disabled"}},
    )
    summary, per_row = ragas_score(
        rows, evaluator, _build_evaluator_embeddings(), SCORE_FILE,
    )
    metrics = (
        "faithfulness", "answer_relevancy", "context_precision",
        "context_recall", "answer_correctness",
    )
    missing = [
        f"{row['id']}:{metric}"
        for row in per_row for metric in metrics if row.get(metric) is None
    ]
    if missing:
        raise RuntimeError(
            f"RAGAS有{len(missing)}项评分未完成，可续跑："
            + ", ".join(missing[:10])
        )
    summary["completed"] = len(rows)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=(
        "retrieval", "reassemble", "generation", "ragas", "all",
    ),
                        default="retrieval")
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--base-url", default="http://[::1]:8000")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    if args.ids:
        selected = set(args.ids)
        cases = [case for case in cases if case["id"] in selected]
    if args.limit is not None:
        cases = cases[:args.limit]
    if not cases:
        parser.error("没有匹配的测试题")
    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
    fingerprint = _fingerprint()
    if args.phase in {"retrieval", "all"}:
        _retrieve(cases, cache, args.base_url, fingerprint, refresh=args.refresh)
    if args.phase == "reassemble":
        _reassemble(cases, cache, fingerprint)
    if args.phase in {"generation", "all"}:
        _generate(cases, cache, fingerprint)
    report = {"retrieval": _summary(cases, cache, fingerprint)}
    if args.phase in {"ragas", "all"}:
        report["ragas"] = _ragas(cases, cache, fingerprint)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"agentic_unified_{datetime.now():%Y%m%d_%H%M%S}.json"
    _save(path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"报告：{path}")


if __name__ == "__main__":
    main()
