#!/usr/bin/env python3
"""Paired 200-question evaluation of ordinary and Agentic RAG."""

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import (  # noqa: E402
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEFAULT_RETRIEVAL_MODE,
)
from agentic.assembly import (  # noqa: E402
    assemble_answer_context,
    refine_answer_context,
    select_agentic_evidence,
)
from tests.eval import reciprocal_rank  # noqa: E402
from tests.ragas_eval import (  # noqa: E402
    _build_evaluator_embeddings,
    ragas_score,
)

CASES_FILE = ROOT / "tests" / "test_cases_with_ref.json"
CACHE_FILE = ROOT / "tests" / "agentic_200_cache.json"
BAILIAN_RETRIEVAL_FILE = ROOT / "tests" / "bailian_retrieval_cache.json"
REPORTS_DIR = ROOT / "tests" / "reports"
SOURCE_FILES = [
    ROOT / "pipeline" / "retrieve.py",
    ROOT / "pipeline" / "generate.py",
    *sorted((ROOT / "agentic").glob("*.py")),
]


def _is_insufficient_balance(exc: Exception) -> bool:
    message = str(exc).lower()
    return "insufficient balance" in message or "余额不足" in message


def _fingerprint() -> str:
    digest = hashlib.sha256()
    digest.update(f"{DEEPSEEK_MODEL}|{DEFAULT_RETRIEVAL_MODE}|top5".encode())
    for path in SOURCE_FILES:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _save(path: Path, data) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    temporary.replace(path)


def _post(base_url: str, route: str, payload: dict) -> dict:
    request = Request(
        f"{base_url.rstrip('/')}{route}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    for attempt in range(3):
        try:
            with urlopen(request, timeout=180) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError) as exc:
            if attempt == 2:
                raise RuntimeError(f"{route} failed: {exc}") from exc
            time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def _answer_chunks(items: list[dict]) -> list[dict]:
    return [
        {
            "content": item["content"],
            "source": item["source"],
            "doc_type": item.get("doc_type") or item.get("evidence_type", ""),
            "H1": item.get("section") or item.get("H1"),
        }
        for item in items
    ]


def _summary(cases: list[dict], cache: dict) -> dict:
    summary = {}
    for arm in ("ordinary", "agentic", "agentic_assembled", "agentic_refined", "bailian"):
        rows = [cache.get(case["id"], {}).get(arm, {}) for case in cases]
        pairs = [
            (case, row)
            for case, row in zip(cases, rows)
            if row.get("hits") is not None
        ]
        n = len(pairs)
        summary[arm] = {
            "completed": n,
            "hit_at_1": sum(
                reciprocal_rank(case["expected_sources"], row["hits"]) == 1
                for case, row in pairs
            ) / n if n else None,
            "hit_at_3": sum(
                reciprocal_rank(case["expected_sources"], row["hits"][:3]) > 0
                for case, row in pairs
            ) / n if n else None,
            "hit_at_5": sum(
                reciprocal_rank(case["expected_sources"], row["hits"][:5]) > 0
                for case, row in pairs
            ) / n if n else None,
            "mrr": sum(
                reciprocal_rank(case["expected_sources"], row["hits"])
                for case, row in pairs
            ) / n if n else None,
        }
    return summary


def _run_retrieval(cases: list[dict], cache: dict, base_url: str) -> None:
    fingerprint = _fingerprint()
    for index, case in enumerate(cases, 1):
        row = cache.setdefault(case["id"], {})
        if row.get("fingerprint") != fingerprint or row.get("question") != case["question"]:
            row.clear()
            row.update({"fingerprint": fingerprint, "question": case["question"]})
        for arm in ("ordinary", "agentic"):
            if row.get(arm, {}).get("hits") is not None:
                continue
            print(f"[{index}/{len(cases)}] {case['id']} {arm}", flush=True)
            try:
                if arm == "ordinary":
                    response = _post(
                        base_url, "/retrieve", {"query": case["question"], "top_k": 5},
                    )
                    hits = response["results"]
                    row[arm] = {"hits": hits}
                else:
                    response = _post(
                        base_url,
                        "/agentic-retrieve",
                        {
                            "query": case["question"],
                            "max_follow_up_rounds": 1,
                            "knowledge_top_k": 4,
                            "case_top_k": 3,
                            "include_trace": True,
                        },
                    )
                    hits = select_agentic_evidence(response)
                    row[arm] = {
                        "hits": hits,
                        "sufficient": response["sufficient"],
                        "rounds": response["rounds_completed"],
                        "evidence_count": len(response["evidence"]),
                    }
                _save(CACHE_FILE, cache)
            except Exception as exc:
                row[f"{arm}_error"] = str(exc)
                _save(CACHE_FILE, cache)
                print(f"  ERROR: {exc}", flush=True)
                if _is_insufficient_balance(exc):
                    raise RuntimeError("余额不足，检索评测已暂停；缓存可续跑") from exc


def _run_generation(cases: list[dict], cache: dict, arms=("ordinary", "agentic_assembled")) -> None:
    from pipeline.generate import rag_answer

    for index, case in enumerate(cases, 1):
        row = cache.get(case["id"], {})
        for arm in arms:
            arm_row = row.get(arm, {})
            if arm_row.get("hits") is None or arm_row.get("answer"):
                continue
            print(f"[{index}/{len(cases)}] {case['id']} {arm} answer", flush=True)
            try:
                arm_row["answer"] = rag_answer(
                    case["question"], _answer_chunks(arm_row["hits"]),
                )
                _save(CACHE_FILE, cache)
            except Exception as exc:
                arm_row["answer_error"] = str(exc)
                _save(CACHE_FILE, cache)
                print(f"  ERROR: {exc}", flush=True)
                if _is_insufficient_balance(exc):
                    raise RuntimeError("余额不足，答案生成已暂停；缓存可续跑") from exc


def _load_bailian_hits(cases: list[dict], cache: dict) -> None:
    retrieval = {
        row["id"]: row
        for row in json.loads(BAILIAN_RETRIEVAL_FILE.read_text(encoding="utf-8"))
    }
    for case in cases:
        source = retrieval.get(case["id"])
        if source is None or source.get("question") != case["question"]:
            raise RuntimeError(f"百炼检索缓存与题目不匹配：{case['id']}")
        hits = [
            {
                "content": result["text"],
                "source": str(result.get("filename") or result.get("file_id") or "百炼知识库") + ".md",
                "doc_type": "bailian_retrieval",
                "chunk_idx": index,
                "distance": 1.0,
            }
            for index, result in enumerate(source.get("results", [])[:5])
            if str(result.get("text", "")).strip()
        ]
        if not hits:
            raise RuntimeError(f"百炼检索缓存缺少有效片段：{case['id']}")
        row = cache.setdefault(case["id"], {})
        if row.get("bailian", {}).get("hits") != hits:
            row["bailian"] = {"hits": hits}
            _save(CACHE_FILE, cache)


def _require_complete_answers(cases: list[dict], cache: dict, arms=("ordinary", "agentic_assembled")) -> None:
    for arm in arms:
        missing = [
            case["id"] for case in cases
            if not cache.get(case["id"], {}).get(arm, {}).get("answer")
        ]
        if missing:
            raise RuntimeError(
                f"{arm} 仅完成 {len(cases) - len(missing)}/{len(cases)} 题，"
                f"不能进行完整 RAGAS 评测；缺失题号：{', '.join(missing[:10])}"
            )


def _run_ragas(cases: list[dict], cache: dict, arms=("ordinary", "agentic_assembled")) -> dict:
    from openai import AsyncOpenAI
    from ragas.evaluation import llm_factory

    result = {}
    llm = llm_factory(
        DEEPSEEK_MODEL,
        client=AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        ),
        max_tokens=8192,
        extra_body={"thinking": {"type": "disabled"}},
    )
    embeddings = _build_evaluator_embeddings()
    for arm in arms:
        rows = []
        for case in cases:
            arm_row = cache.get(case["id"], {}).get(arm, {})
            if not arm_row.get("answer"):
                continue
            rows.append({
                "id": case["id"],
                "user_input": case["question"],
                "retrieved_contexts": [
                    hit["content"] for hit in arm_row["hits"]
                ],
                "response": arm_row["answer"],
                "reference": case["reference"],
            })
        score_file = ROOT / "tests" / f"agentic_200_ragas_{arm}_v2.json"
        result[arm], per_row = ragas_score(rows, llm, embeddings, score_file)
        missing = [
            f"{row['id']}:{metric}"
            for row in per_row
            for metric in ("faithfulness", "answer_relevancy", "context_precision", "context_recall", "answer_correctness")
            if row.get(metric) is None
        ]
        if missing:
            raise RuntimeError(
                f"{arm} 的 RAGAS 评分缺失 {len(missing)} 项，续跑可重试："
                + ", ".join(missing[:10])
            )
        result[arm]["completed"] = len(rows)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["retrieval", "assembly", "generation", "ragas", "finish", "all", "refine", "refined-generation", "refined-ragas", "refined-finish", "refined-all", "bailian-cache", "bailian-finish"], default="retrieval")
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--base-url", default="http://[::1]:8000")
    args = parser.parse_args()

    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    if args.ids:
        selected = set(args.ids)
        cases = [case for case in cases if case["id"] in selected]
    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
    if args.phase in {"retrieval", "all"}:
        _run_retrieval(cases, cache, args.base_url)
    if args.phase in {"assembly", "all"}:
        for case in cases:
            row = cache.get(case["id"], {})
            ordinary = row.get("ordinary", {}).get("hits")
            agentic = row.get("agentic", {}).get("hits")
            if ordinary is None or agentic is None:
                continue
            row["agentic_assembled"] = {
                "hits": assemble_answer_context(case["question"], ordinary, agentic),
            }
            _save(CACHE_FILE, cache)
    if args.phase in {"refine", "refined-all"}:
        for case in cases:
            row = cache.get(case["id"], {})
            assembled = row.get("agentic_assembled", {}).get("hits")
            if assembled is None:
                continue
            row["agentic_refined"] = {
                "hits": refine_answer_context(case["question"], assembled),
            }
            _save(CACHE_FILE, cache)
    if args.phase in {"bailian-cache", "bailian-finish"}:
        _load_bailian_hits(cases, cache)
    if args.phase in {"generation", "finish", "all"}:
        _run_generation(cases, cache)
    if args.phase in {"refined-generation", "refined-finish", "refined-all"}:
        _run_generation(cases, cache, arms=("agentic_refined",))
    if args.phase == "bailian-finish":
        _run_generation(cases, cache, arms=("bailian",))
    summary = {"retrieval": _summary(cases, cache)}
    if args.phase in {"ragas", "finish", "all"}:
        _require_complete_answers(cases, cache)
        summary["ragas"] = _run_ragas(cases, cache)
    if args.phase in {"refined-ragas", "refined-finish", "refined-all"}:
        _require_complete_answers(cases, cache, arms=("agentic_refined",))
        summary["ragas"] = _run_ragas(cases, cache, arms=("agentic_refined",))
    if args.phase == "bailian-finish":
        _require_complete_answers(cases, cache, arms=("bailian",))
        summary["ragas"] = _run_ragas(cases, cache, arms=("bailian",))
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"agentic_200_comparison_{datetime.now():%Y%m%d_%H%M%S}.json"
    _save(path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"报告：{path}")


if __name__ == "__main__":
    main()
