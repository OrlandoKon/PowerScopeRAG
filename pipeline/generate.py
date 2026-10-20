"""
pipeline/generate.py — RAG generation layer using DeepSeek.

Takes a user query + retrieved chunks, calls DeepSeek to produce a grounded answer.
API key is read from the DEEPSEEK_API_KEY environment variable.
"""

import json
import logging

from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

_SYSTEM_PROMPT = """你是一个专业的电力系统分析助手，专注于400V低压配电网的停电原因分析。

你的任务：
1. 根据下方提供的【知识库参考片段】回答用户的问题
2. 答案必须基于参考片段中的内容，引用具体的标准条款或数据
3. 如果参考片段不足以回答问题，直接说明"知识库中暂无相关内容"，不要编造
4. 涉及数值阈值（如电压偏差、不平衡度限值）时，直接给出具体数字
5. 回答简洁、准确，使用中文
6. 按提问顺序逐项回答，只保留直接答案、必要条件和来源；不要补充未询问的背景、其他数值或重复解释

知识库参考片段如下：
{context}"""

_REVIEW_PROMPT = """你是电力知识库问答的答案校验编辑。只能依据所给参考片段修订草稿，不得使用外部知识。

先在心中逐项核对问题与草稿，然后只输出最终答案：
1. 草稿已经直接、准确回答全部问题且没有多余事实时，原样输出。
2. 若草稿遗漏了片段直接支持的答案，补齐该子项；若加入未被询问的其他标准、场景、数值或背景，删掉。
3. 保留正确的关键数值、单位、适用对象及条件；不能把不同来源、不同适用范围的要求混为一谈。
4. 仅对确实缺乏证据的子项简短说明不足，不要以整题拒答取代可回答部分，也不要为贴合问题而编造。
5. 来源至多一句。不得附加编辑说明、检查过程或问题复述。

知识库参考片段：
{context}"""

_DIAGNOSIS_SYSTEM_PROMPT = """你是一个专业的400V低压配电网停电原因分析助手。

你会收到两类证据：
1. 【标准、规程与论文证据】：用于判断阈值、动作逻辑和技术依据。
2. 【相似案例】：用于类比历史现象，不得替代标准依据。案例若标记为“待专家确认”，必须明确说明其仅供参考。

回答要求：
1. 给出按可能性排序的候选停电原因，并引用支持每个候选原因的具体证据。
2. 明确区分“规范依据”和“案例类比”，不得把相似案例当作当前事件已经确认的事实。
3. 指出当前仍缺少的关键数据，以及下一步应检查的系统或设备。
4. 证据不足时输出“当前证据不足以确定唯一原因”，不得编造数值、告警或现场结论。
5. 使用简洁中文。

【标准、规程与论文证据】
{knowledge_context}

【相似案例】
{case_context}"""

_client: OpenAI | None = None
_logger = logging.getLogger(__name__)


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
        if c.get("context_format") == "structured":
            parts.append(f"[{i}]\n{c['content']}")
            continue
        heading = " > ".join(c[h] for h in ("H1", "H2", "H3") if c.get(h))
        source_line = f"[{i}] 来源：{c['source']}"
        if heading:
            source_line += f"  章节：{heading}"
        parts.append(f"{source_line}\n{c['content']}")
    return "\n\n---\n\n".join(parts)


def _format_cases(cases: list[dict]) -> str:
    if not cases:
        return "未检索到相似案例。"
    parts = []
    for i, case in enumerate(cases, 1):
        title = (
            f"[{i}] {case.get('case_id', '')} "
            f"{case.get('cause_code', '')} {case.get('cause_name', '')}"
        ).strip()
        status = case.get("verification_status", "未知")
        parts.append(
            f"{title}\n核验状态：{status}\n{case.get('content', case.get('case_text', ''))}"
        )
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


def review_answer(query: str, chunks: list[dict], draft: str) -> str:
    """Revise a generated answer against the same evidence, preserving it on failure."""
    if not draft.strip() or not chunks:
        return draft
    try:
        resp = _get_client().chat.completions.create(
            model=DEEPSEEK_MODEL,
            timeout=30,
            messages=[
                {"role": "system", "content": _REVIEW_PROMPT.format(
                    context=_format_context(chunks),
                )},
                {"role": "user", "content": json.dumps(
                    {"question": query, "draft": draft}, ensure_ascii=False,
                )},
            ],
        )
    except Exception:
        _logger.exception("Answer review failed; returning original draft")
        return draft
    return (resp.choices[0].message.content or "").strip() or draft


def rag_diagnose(
    query: str,
    knowledge_chunks: list[dict],
    case_chunks: list[dict],
) -> str:
    """Generate a diagnosis using authoritative knowledge and analogous cases."""
    client = _get_client()
    system = _DIAGNOSIS_SYSTEM_PROMPT.format(
        knowledge_context=_format_context(knowledge_chunks) if knowledge_chunks else "未检索到相关规范证据。",
        case_context=_format_cases(case_chunks),
    )
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": query},
        ],
    )
    return resp.choices[0].message.content
