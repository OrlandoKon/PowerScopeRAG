"""Generate DeepSeek answers from cached Bailian Top-5 retrieval contexts.

This isolates retrieval quality by using the same ``pipeline.generate.rag_answer``
prompt and DeepSeek model as PowerScopeRAG while keeping Bailian's ranked chunks.
Answers are checkpointed after every completed question.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import DEEPSEEK_MODEL  # noqa: E402
from pipeline.generate import _SYSTEM_PROMPT, _get_client, rag_answer  # noqa: E402

REF_FILE = ROOT / "tests" / "test_cases_with_ref.json"
RETRIEVAL_CACHE_FILE = ROOT / "tests" / "bailian_retrieval_cache.json"
ANSWER_CACHE_FILE = ROOT / "tests" / "bailian_deepseek_answer_cache.json"

GENERATION_FINGERPRINT = hashlib.sha256(
    f"{DEEPSEEK_MODEL}\n{_SYSTEM_PROMPT}".encode("utf-8")
).hexdigest()[:16]


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def retrieval_fingerprint(case: dict, row: dict) -> str:
    payload = {
        "question": case["question"],
        "results": [
            {
                "filename": result.get("filename", ""),
                "text": result.get("text", ""),
            }
            for result in row.get("results", [])[:5]
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def to_generation_chunks(row: dict) -> list[dict]:
    chunks = []
    for index, result in enumerate(row.get("results", [])[:5]):
        text = str(result.get("text", "")).strip()
        if not text:
            continue
        chunks.append({
            "content": text,
            "source": result.get("filename") or result.get("file_id") or "百炼知识库",
            "doc_type": "bailian_retrieval",
            "chunk_idx": index,
        })
    return chunks


def save_cache(cache: dict[str, dict], case_order: dict[str, int]) -> None:
    ordered = sorted(
        cache.values(),
        key=lambda row: (case_order.get(row["id"], len(case_order)), row["id"]),
    )
    atomic_write_json(ANSWER_CACHE_FILE, ordered)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="使用相同DeepSeek提示词从百炼Top-5切片生成答案"
    )
    parser.add_argument("--ids", nargs="+", help="只处理指定题号")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers必须大于等于1")

    all_cases: list[dict] = load_json(REF_FILE, [])
    cases = all_cases
    if args.ids:
        requested = set(args.ids)
        cases = [case for case in all_cases if case["id"] in requested]
    if not cases:
        parser.error("没有可生成答案的题目")

    retrieval = {
        row["id"]: row for row in load_json(RETRIEVAL_CACHE_FILE, []) if row.get("id")
    }
    missing = [
        case["id"] for case in cases
        if case["id"] not in retrieval
        or retrieval[case["id"]].get("question") != case["question"]
        or not to_generation_chunks(retrieval[case["id"]])
    ]
    if missing:
        parser.error(f"百炼检索缓存缺少当前题目或有效切片：{' '.join(missing[:20])}")

    case_order = {case["id"]: index for index, case in enumerate(all_cases)}
    cache = {
        row["id"]: row for row in load_json(ANSWER_CACHE_FILE, []) if row.get("id")
    }
    todo = []
    for case in cases:
        source_row = retrieval[case["id"]]
        fingerprint = retrieval_fingerprint(case, source_row)
        cached = cache.get(case["id"], {})
        valid = (
            not args.refresh
            and cached.get("question") == case["question"]
            and cached.get("retrieval_fingerprint") == fingerprint
            and cached.get("generation_fingerprint") == GENERATION_FINGERPRINT
            and cached.get("answer_model") == DEEPSEEK_MODEL
            and cached.get("answer_complete") is True
            and str(cached.get("answer", "")).strip()
        )
        if not valid:
            todo.append((case, source_row, fingerprint))

    print(f"DeepSeek答案待生成：{len(todo)}题，已缓存：{len(cases)-len(todo)}题")
    if not todo:
        return

    _get_client()

    def generate_one(item: tuple[dict, dict, str]) -> tuple[dict, float]:
        case, source_row, fingerprint = item
        started = time.time()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                answer = rag_answer(case["question"], to_generation_chunks(source_row))
                if not str(answer).strip():
                    raise RuntimeError("DeepSeek返回空答案")
                return ({
                    **source_row,
                    "id": case["id"],
                    "question": case["question"],
                    "answer": str(answer).strip(),
                    "answer_complete": True,
                    "answer_model": DEEPSEEK_MODEL,
                    "model": DEEPSEEK_MODEL,
                    "generation_mode": "bailian_retrieval_deepseek_generation",
                    "retrieval_fingerprint": fingerprint,
                    "generation_fingerprint": GENERATION_FINGERPRINT,
                    "completed": True,
                }, time.time() - started)
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(5)
        raise RuntimeError(f"{case['id']}生成失败：{last_error}")

    completed = len(cases) - len(todo)
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(generate_one, item): item[0]["id"] for item in todo}
        for future in as_completed(futures):
            case_id = futures[future]
            try:
                row, elapsed = future.result()
            except Exception as exc:
                errors.append(str(exc))
                print(f"  {case_id} 失败：{exc}", flush=True)
                continue
            cache[case_id] = row
            completed += 1
            save_cache(cache, case_order)
            print(
                f"  [{completed}/{len(cases)}] {case_id} "
                f"答案{len(row['answer'])}字，{elapsed:.1f}s",
                flush=True,
            )

    if errors:
        raise RuntimeError(f"{len(errors)}题生成失败，重新运行可断点续跑")
    print(f"答案缓存：{ANSWER_CACHE_FILE}")


if __name__ == "__main__":
    main()
