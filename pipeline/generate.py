"""
pipeline/generate.py — RAG generation layer using DeepSeek.

Takes a user query + retrieved chunks, calls DeepSeek to produce a grounded answer.
API key is read from the DEEPSEEK_API_KEY environment variable.
"""

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

_SYSTEM_PROMPT = """你是一个专业的电力系统分析助手，专注于400V低压配电网的停电原因分析。

你的任务：
1. 根据下方提供的【知识库参考片段】回答用户的问题
2. 答案必须基于参考片段中的内容，引用具体的标准条款或数据
3. 如果参考片段不足以回答问题，直接说明"知识库中暂无相关内容"，不要编造
4. 涉及数值阈值（如电压偏差、不平衡度限值）时，直接给出具体数字
5. 回答简洁、准确，使用中文

知识库参考片段如下：
{context}"""

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is not None:
        return _client
    if not DEEPSEEK_API_KEY:
        raise RuntimeError(
            "DEEPSEEK_API_KEY environment variable not set. "
            "Add it to the systemd service or export it in your shell."
        )
    _client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    return _client


def _format_context(chunks: list[dict]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        heading = " > ".join(c[h] for h in ("H1", "H2", "H3") if c.get(h))
        source_line = f"[{i}] 来源：{c['source']}"
        if heading:
            source_line += f"  章节：{heading}"
        parts.append(f"{source_line}\n{c['content']}")
    return "\n\n---\n\n".join(parts)


def rag_answer(
    query: str,
    chunks: list[dict],
    stream: bool = False,
) -> str:
    """
    Generate an answer grounded in the retrieved chunks.

    Args:
        query:  The user's question.
        chunks: Retrieved chunk dicts (from retrieve.retrieve()).
        stream: If True, print tokens as they arrive and return full text.

    Returns:
        The model's answer as a string.
    """
    client = _get_client()
    context = _format_context(chunks)
    system = _SYSTEM_PROMPT.format(context=context)

    if stream:
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": query},
            ],
            stream=True,
        )
        full = []
        for chunk in resp:
            token = chunk.choices[0].delta.content or ""
            print(token, end="", flush=True)
            full.append(token)
        print()
        return "".join(full)
    else:
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": query},
            ],
        )
        return resp.choices[0].message.content
