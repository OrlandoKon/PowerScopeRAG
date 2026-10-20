"""Shared title- and heading-aware text representation for retrieval."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path


DOC_TYPE_LABELS = {
    "regulation": "标准规范",
    "journal": "期刊论文",
    "thesis": "学位论文",
    "case": "历史案例",
}

_NORMATIVE_QUERY_SIGNALS = (
    "标准",
    "规范",
    "要求",
    "规定",
    "不得",
    "不应",
    "应按",
    "应符合",
    "限值",
    "上限",
    "下限",
    "不低于",
    "不高于",
    "不小于",
    "不大于",
    "至少",
    "必须",
)
_PAPER_QUERY_SIGNALS = ("论文", "研究中", "作者", "提出")


def source_title(source: str) -> str:
    """Turn a stored source filename into searchable title and author text."""
    title = unicodedata.normalize("NFKC", Path(source).stem)
    title = re.sub(r"[_+]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def source_author(source: str) -> str:
    """Extract the trailing Chinese author token used by paper filenames."""
    stem = unicodedata.normalize("NFKC", Path(source).stem)
    if "_" not in stem:
        return ""
    candidate = stem.rsplit("_", 1)[-1].strip()
    if re.fullmatch(r"[\u3400-\u9fff·]{2,6}", candidate):
        return candidate
    return ""


def metadata_match_score(query: str, chunk: dict) -> float:
    """Score explicit author matches and unambiguous normative query intent."""
    source = str(chunk.get("source", ""))
    author = source_author(source)
    if author and author in query:
        return 1.0
    if "雄安新区" in query and source.startswith("DB1331"):
        return 0.9
    is_normative = any(signal in query for signal in _NORMATIVE_QUERY_SIGNALS)
    is_paper_question = any(signal in query for signal in _PAPER_QUERY_SIGNALS)
    if chunk.get("doc_type") == "regulation" and is_normative and not is_paper_question:
        return 0.25
    return 0.0


def heading_path(chunk: dict) -> str:
    """Return the H1/H2/H3 path carried by a chunk."""
    return " > ".join(
        str(chunk[key]).strip()
        for key in ("H1", "H2", "H3")
        if str(chunk.get(key, "")).strip()
    )


def section_key(chunk: dict) -> str:
    """Stable key used to aggregate chunks at section level."""
    return f"{chunk.get('source', '')}::{heading_path(chunk) or '__root__'}"


def build_index_text(chunk: dict) -> str:
    """Combine source, structure and body for embedding, BM25 and reranking."""
    parts = [f"文档标题：{source_title(str(chunk.get('source', '')))}"]
    doc_type = str(chunk.get("doc_type", "")).strip()
    if doc_type:
        parts.append(f"文档类型：{DOC_TYPE_LABELS.get(doc_type, doc_type)}")
    headings = heading_path(chunk)
    if headings:
        parts.append(f"章节路径：{headings}")
    parts.append(f"正文：{str(chunk.get('content', '')).strip()}")
    return "\n".join(parts)
