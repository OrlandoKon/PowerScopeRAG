"""
tests/gen_reference.py — 为评测题库生成参考答案

思路（与 RAGAS TestsetGenerator 一致）：
  对每道题，从 expected_sources 文档中取最相关 chunk，
  以"完美检索"视角调用 DeepSeek 生成参考答案（reference）。

输出：tests/test_cases_with_ref.json
  在原 test_cases.json 每条上追加:
    "reference"          : str   — 参考答案
    "reference_contexts" : [str] — 生成参考答案所用的 chunk 文本

用法:
    python tests/gen_reference.py               # 全部 200 题
    python tests/gen_reference.py --ids Q001 Q002  # 调试单题
    python tests/gen_reference.py --start 0 --end 50  # 分批跑
"""

import argparse
import json
import sys
import time
from pathlib import Path

import jieba
from rank_bm25 import BM25Okapi
from openai import OpenAI

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import CHUNKS_FILE, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

CASES_FILE = ROOT / "tests" / "test_cases.json"
OUT_FILE   = ROOT / "tests" / "test_cases_with_ref.json"
TOP_CTX    = 5   # 每道题取几个 chunk 作为参考 context

jieba.setLogLevel(20)

_SYSTEM = """你是一个专业的电力系统分析助手。
根据下方提供的【知识库参考片段】，准确、完整地回答用户问题。
要求：
1. 答案只基于参考片段，不得引入片段外的内容
2. 涉及具体数值、标准条款时直接引用
3. 简洁、准确，使用中文

知识库参考片段：
{context}"""


def _load_chunks_by_source(sources: list[str]) -> list[dict]:
    """从 chunks.jsonl 加载属于指定 source 的所有 chunk。"""
    result = []
    with open(CHUNKS_FILE, encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            if c["source"] in sources:
                result.append(c)
    return result


def _top_chunks_bm25(query: str, chunks: list[dict], top_k: int) -> list[dict]:
    """在给定 chunk 列表内用 BM25 找最相关的 top_k 条。"""
    if not chunks:
        return []
    tokenized = [list(jieba.cut(c["content"])) for c in chunks]
    bm25 = BM25Okapi(tokenized)
    scores = bm25.get_scores(list(jieba.cut(query)))
    ranked = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
    return [chunks[i] for i in ranked[:top_k]]


def _format_context(chunks: list[dict]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        heading = " > ".join(c[h] for h in ("H1", "H2", "H3") if c.get(h))
        line = f"[{i}] 来源：{c['source']}"
        if heading:
            line += f"  章节：{heading}"
        parts.append(f"{line}\n{c['content']}")
    return "\n\n---\n\n".join(parts)


def generate_reference(query: str, sources: list[str], client: OpenAI) -> tuple[str, list[str]]:
    """返回 (reference_answer, reference_context_texts)。"""
    chunks = _load_chunks_by_source(sources)
    top = _top_chunks_bm25(query, chunks, TOP_CTX)
    if not top:
        return "知识库中暂无相关内容", []
    context = _format_context(top)
    system = _SYSTEM.format(context=context)
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": query},
        ],
    )
    answer = resp.choices[0].message.content.strip()
    ctx_texts = [c["content"] for c in top]
    return answer, ctx_texts


def main():
    parser = argparse.ArgumentParser(description="生成评测参考答案")
    parser.add_argument("--ids",   nargs="+", metavar="ID", help="只处理指定题目")
    parser.add_argument("--start", type=int, default=0,    help="起始索引（分批跑）")
    parser.add_argument("--end",   type=int, default=None, help="结束索引（分批跑，不含）")
    args = parser.parse_args()

    if not DEEPSEEK_API_KEY:
        print("错误：DEEPSEEK_API_KEY 未设置，请先在 .env 文件中配置")
        sys.exit(1)

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

    cases: list[dict] = json.loads(CASES_FILE.read_text(encoding="utf-8"))

    # 读已有结果（支持断点续跑）
    existing: dict[str, dict] = {}
    if OUT_FILE.exists():
        for item in json.loads(OUT_FILE.read_text(encoding="utf-8")):
            existing[item["id"]] = item

    # 过滤要处理的题目
    if args.ids:
        cases = [c for c in cases if c["id"] in args.ids]
    else:
        end = args.end or len(cases)
        cases = cases[args.start:end]

    # 跳过已有答案的题目
    todo = [c for c in cases if c["id"] not in existing or "reference" not in existing[c["id"]]]
    print(f"待生成：{len(todo)} 题 / 已有：{len(existing)} 题")

    t0 = time.time()
    for i, case in enumerate(todo):
        qid = case["id"]
        print(f"[{i+1}/{len(todo)}] {qid}  {case['question'][:50]}...", end=" ", flush=True)
        try:
            ref, ctx = generate_reference(case["question"], case["expected_sources"], client)
            result = {**case, "reference": ref, "reference_contexts": ctx}
            existing[qid] = result
            print(f"✅ ({len(ref)}字)")
        except Exception as e:
            print(f"❌ {e}")
        # 每 10 题保存一次（断点续跑）
        if (i + 1) % 10 == 0:
            _save(existing)
            print(f"  → 已保存 {OUT_FILE.name}")

    _save(existing)
    elapsed = time.time() - t0
    print(f"\n完成！共 {len(existing)} 条，耗时 {elapsed:.0f}s → {OUT_FILE}")


def _save(data: dict):
    cases_order = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    ordered = [data[c["id"]] for c in cases_order if c["id"] in data]
    OUT_FILE.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
