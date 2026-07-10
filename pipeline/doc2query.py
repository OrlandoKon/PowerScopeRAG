"""
pipeline/doc2query.py — Generate and filter doc2query expansions for chunks.

This is an offline index-expansion step:

1. Read original chunks from data/chunks.jsonl.
2. Ask the LLM to generate likely user questions and grounded answers for each chunk.
3. Filter generic / duplicate questions.
4. Optionally run full-corpus retrieval backcheck: a generated question is kept only
   if the original chunk/source can be retrieved near the top.
5. Write:
   - data/doc2query.jsonl         generation + filter audit records
   - data/chunks_doc2query.jsonl  chunks with indexed_content for embedding

The original chunk content is preserved. The augmented text is used only for
retrieval indexing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import (  # noqa: E402
    CHUNKS_FILE,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DOC2QUERY_CHUNKS_FILE,
    DOC2QUERY_FILE,
)


SYSTEM_PROMPT = """你是电力系统知识库的检索数据构造助手。

任务：只根据给定 chunk 生成用户可能提出的问题，以及该 chunk 能直接支持的标准答案。

要求：
1. 问题必须具体，尽量包含对象、场景、标准名/论文主题、关键条件或数值类型。
2. 不要生成“本文主要讲了什么”“有哪些要求”这类泛泛问题。
3. 不要询问文档类型、来源文件、作者信息、论文属于什么类型等元数据问题。
4. 标准答案必须完全来自 chunk，不得补充外部知识。
5. 如果 chunk 信息不足以形成高质量问题，返回空数组；不要生成答案为“文中未说明”的问题。
6. 只输出 JSON，不要输出 Markdown。

JSON 格式：
{
  "items": [
    {"question": "...", "answer": "..."}
  ]
}
"""


GENERIC_PATTERNS = [
    "主要内容", "主要讲", "主要介绍", "有哪些内容", "有哪些要求", "是什么",
    "技术要求是什么", "有什么意义", "作用是什么", "说明什么",
]
EXACT_GENERIC_QUESTIONS = {
    "这段内容主要讲了什么？",
    "本文主要讲了什么？",
    "该标准主要规定了什么？",
    "该章节主要内容是什么？",
}
BAD_QUESTION_PATTERNS = [
    "属于什么类型", "来源文件", "文档类型", "作者是谁", "论文类型",
]
BAD_ANSWER_PATTERNS = [
    "未具体说明", "未说明", "未提及", "没有说明", "无法确定", "暂无相关",
]


def load_chunks(path: Path) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def chunk_id(chunk: dict[str, Any]) -> str:
    return f"{chunk['source']}::{chunk['chunk_idx']}"


def normalize_question(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[ \t\r\n]+", "", text)
    text = re.sub(r"[？?。.!！,，、；;：:（）()《》“”\"'`]", "", text)
    return text


def is_generic_question(question: str) -> bool:
    q = question.strip()
    if len(q) < 12:
        return True
    if q in EXACT_GENERIC_QUESTIONS:
        return True
    # "是什么" is only generic when it is the whole question frame; it is valid
    # in domain questions such as "电压偏差限值是什么".
    weak_patterns = {"是什么"}
    return any(p in q for p in GENERIC_PATTERNS if p not in weak_patterns)


def invalid_item(question: str, answer: str) -> str | None:
    if is_generic_question(question):
        return "generic"
    if any(p in question for p in BAD_QUESTION_PATTERNS):
        return "metadata_question"
    if any(p in answer for p in BAD_ANSWER_PATTERNS):
        return "unsupported_answer"
    return None


def heading_text(chunk: dict[str, Any]) -> str:
    return " > ".join(str(chunk[h]) for h in ("H1", "H2", "H3") if chunk.get(h))


def prompt_for_chunk(chunk: dict[str, Any], n: int) -> str:
    heading = heading_text(chunk) or "无"
    return f"""请为下面 chunk 生成 {n} 个高质量候选问题及其标准答案。

来源文件：{chunk['source']}
文档类型：{chunk.get('doc_type', '')}
章节：{heading}

chunk 内容：
{chunk['content']}
"""


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM response does not contain a JSON object")
    return json.loads(text[start : end + 1])


def llm_generate(client: OpenAI, chunk: dict[str, Any], n: int, retries: int = 3) -> list[dict[str, str]]:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt_for_chunk(chunk, n)},
            ]
            try:
                resp = client.chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=messages,
                    temperature=0.2,
                    response_format={"type": "json_object"},
                )
            except Exception as exc:
                # Some OpenAI-compatible providers do not support response_format.
                if "response_format" not in str(exc):
                    raise
                resp = client.chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=messages,
                    temperature=0.2,
                )
            data = parse_json_object(resp.choices[0].message.content or "")
            items = data.get("items", [])
            out: list[dict[str, str]] = []
            for item in items:
                question = str(item.get("question", "")).strip()
                answer = str(item.get("answer", "")).strip()
                if question and answer:
                    out.append({"question": question, "answer": answer})
            return out
        except Exception as exc:  # keep generation resumable
            last_err = exc
            if attempt + 1 < retries:
                time.sleep(3)
    raise RuntimeError(f"doc2query generation failed for {chunk_id(chunk)}: {last_err}")


def backcheck_question(
    question: str,
    chunk: dict[str, Any],
    *,
    top_k: int,
    mode: str,
    max_source_rank: int,
    max_chunk_rank: int,
) -> dict[str, Any]:
    from pipeline.retrieve import retrieve

    hits = retrieve(question, top_k=top_k, mode=mode)
    rank_source = None
    rank_chunk = None
    target = chunk_id(chunk)
    for i, hit in enumerate(hits, 1):
        if rank_source is None and hit.get("source") == chunk["source"]:
            rank_source = i
        if chunk_id(hit) == target:
            rank_chunk = i

    keep = (
        (rank_chunk is not None and rank_chunk <= max_chunk_rank)
        or (rank_source is not None and rank_source <= max_source_rank)
    )
    return {
        "keep": keep,
        "rank_source": rank_source,
        "rank_chunk": rank_chunk,
        "top_sources": [h.get("source") for h in hits[: min(5, len(hits))]],
    }


def load_existing(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    existing: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            existing[rec["chunk_id"]] = rec
    return existing


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def build_augmented_chunks(
    chunks: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    out_path: Path,
) -> int:
    augmented: list[dict[str, Any]] = []
    n_augmented = 0
    for chunk in chunks:
        cid = chunk_id(chunk)
        rec = records.get(cid, {})
        kept = rec.get("kept", [])
        new_chunk = dict(chunk)
        if kept:
            questions = [q["question"] for q in kept]
            answers = [q["answer"] for q in kept]
            expansion = "\n".join(f"- {q}" for q in questions)
            new_chunk["doc2query_questions"] = questions
            new_chunk["doc2query_answers"] = answers
            new_chunk["doc2query_count"] = len(questions)
            new_chunk["indexed_content"] = (
                f"{chunk['content']}\n\n"
                f"【该片段可回答的候选问题】\n{expansion}"
            )
            n_augmented += 1
        else:
            new_chunk["doc2query_count"] = 0
        augmented.append(new_chunk)

    write_jsonl(out_path, augmented)
    return n_augmented


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate doc2query expansions with quality filtering.")
    parser.add_argument("--chunks", type=Path, default=CHUNKS_FILE)
    parser.add_argument("--out", type=Path, default=DOC2QUERY_FILE)
    parser.add_argument("--augmented-out", type=Path, default=DOC2QUERY_CHUNKS_FILE)
    parser.add_argument("--questions-per-chunk", type=int, default=3)
    parser.add_argument("--limit", type=int, help="Only process first N selected chunks")
    parser.add_argument("--ids", nargs="+", help="Only process chunk ids like source.md::12")
    parser.add_argument("--sources", nargs="+", help="Only process selected source filenames")
    parser.add_argument("--resume", action="store_true", help="Reuse existing records in --out")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate selected chunks even if records already exist")
    parser.add_argument("--skip-backcheck", action="store_true", help="Skip full-corpus retrieval validation")
    parser.add_argument("--backcheck-mode", default="hybrid", choices=["dense", "hybrid", "hybrid+rerank"])
    parser.add_argument("--backcheck-top-k", type=int, default=10)
    parser.add_argument("--max-source-rank", type=int, default=3)
    parser.add_argument("--max-chunk-rank", type=int, default=5)
    args = parser.parse_args()

    if not DEEPSEEK_API_KEY:
        print("错误：DEEPSEEK_API_KEY 未设置")
        sys.exit(1)

    chunks = load_chunks(args.chunks)
    selected = chunks
    if args.ids:
        ids = set(args.ids)
        selected = [c for c in selected if chunk_id(c) in ids]
    if args.sources:
        sources = set(args.sources)
        selected = [c for c in selected if c["source"] in sources]
    if args.limit:
        selected = selected[: args.limit]

    existing = load_existing(args.out) if args.resume else {}
    records = dict(existing)
    seen_questions = {
        normalize_question(item["question"])
        for rec in records.values()
        for item in rec.get("kept", [])
    }

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

    print(
        f"Doc2Query generation | selected={len(selected)} / chunks={len(chunks)} "
        f"| resume={len(existing)} | backcheck={not args.skip_backcheck}"
    )

    for i, chunk in enumerate(selected, 1):
        cid = chunk_id(chunk)
        if cid in records and not args.force:
            print(f"  [{i}/{len(selected)}] {cid} skip")
            continue
        if args.force and cid in records:
            old = records.pop(cid)
            for item in old.get("kept", []):
                seen_questions.discard(normalize_question(item["question"]))

        print(f"  [{i}/{len(selected)}] {cid}", flush=True)
        raw_items = llm_generate(client, chunk, args.questions_per_chunk)
        kept: list[dict[str, Any]] = []
        filtered: list[dict[str, Any]] = []

        for item in raw_items:
            norm = normalize_question(item["question"])
            reason = invalid_item(item["question"], item["answer"])
            if reason is None and norm in seen_questions:
                reason = "duplicate"

            backcheck = None
            if reason is None and not args.skip_backcheck:
                backcheck = backcheck_question(
                    item["question"],
                    chunk,
                    top_k=args.backcheck_top_k,
                    mode=args.backcheck_mode,
                    max_source_rank=args.max_source_rank,
                    max_chunk_rank=args.max_chunk_rank,
                )
                if not backcheck["keep"]:
                    reason = "backcheck_failed"

            item_out = {**item, "normalized": norm}
            if backcheck is not None:
                item_out["backcheck"] = backcheck

            if reason is None:
                kept.append(item_out)
                seen_questions.add(norm)
            else:
                filtered.append({**item_out, "reason": reason})

        records[cid] = {
            "chunk_id": cid,
            "source": chunk["source"],
            "chunk_idx": chunk["chunk_idx"],
            "doc_type": chunk.get("doc_type"),
            "heading": heading_text(chunk),
            "raw": raw_items,
            "kept": kept,
            "filtered": filtered,
        }
        write_jsonl(args.out, list(records.values()))
        print(f"      kept={len(kept)} filtered={len(filtered)}")

    n_augmented = build_augmented_chunks(chunks, records, args.augmented_out)
    total_kept = sum(len(r.get("kept", [])) for r in records.values())
    print(f"\n完成：kept_questions={total_kept} augmented_chunks={n_augmented}")
    print(f"  审计文件：{args.out}")
    print(f"  扩展 chunks：{args.augmented_out}")


if __name__ == "__main__":
    main()
