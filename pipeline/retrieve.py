"""
pipeline/retrieve.py — Hybrid retrieval interface for PowerScopeRAG.

Supports three retrieval modes:
  dense          — vector-only (ChromaDB cosine similarity), original behaviour
  hybrid         — dense + BM25 fused via Reciprocal Rank Fusion
  hybrid+rerank  — hybrid + bge-reranker-v2-m3 cross-encoder re-ranking (default)
  *+structured   — title/H1/H2/H3-aware index and reranking
  *+hierarchical — structured retrieval + document/section aggregation and
                    adaptive evidence assembly
  *+doc2query    — use the doc2query-expanded collection/chunks for retrieval
  case retrieval — query the independent structured historical-case collection

BGE query instruction prefix is applied to queries at retrieval time.

Usage:
    from pipeline.retrieve import retrieve
    results = retrieve("供电电压偏差限值")                 # hybrid+rerank (default)
    results = retrieve("台区低电压原因", mode="dense")     # vector-only
    results = retrieve("配变跳闸", top_k=3, mode="hybrid") # hybrid, no rerank
    bundle = retrieve_with_cases("配变负载率132%并跳闸")   # knowledge + cases

    # CLI demo
    python pipeline/retrieve.py "供电电压偏差限值"
    python pipeline/retrieve.py "供电电压偏差限值" 5 "" dense
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import chromadb
import jieba
import jieba.analyse
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from rank_bm25 import BM25Okapi

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import (
    CASES_FILE, CASE_COLLECTION_NAME, CHUNKS_FILE, COLLECTION_NAME, DEFAULT_TOP_K,
    DEFAULT_RETRIEVAL_MODE,
    DOC2QUERY_CHUNKS_FILE,
    DOC2QUERY_COLLECTION_NAME, EMBED_MODEL, QUERY_PREFIX, RERANK_MODEL, RRF_K,
    STRUCTURED_COLLECTION_NAME, VECTORSTORE_DIR, LOCAL_MODEL_DEVICE,
)
from pipeline.index_text import (
    build_index_text,
    heading_path,
    metadata_match_score,
    section_key,
    source_author,
)

jieba.setLogLevel(20)  # suppress jieba initialisation noise

_QUERY_OBJECT_TERMS = tuple(sorted({
    "移动式应急照明", "分布式光伏", "配电变压器", "低压配电网",
    "三相不平衡", "综合管廊", "供电电压", "剩余电流", "无功补偿",
    "历史案例", "应急照明", "低压电缆", "电缆接头", "停电场景",
    "GIS配电装置室", "变压器室", "设备房间", "允许载流量", "最大相电流",
    "公用配电室", "用户配电室", "供电区域", "负荷密度", "设计使用年限",
    "切断时间", "TN系统", "箱变", "监测点", "开关站", "建筑面积",
    "用地面积", "线路电阻", "线路电感", "电压上限", "电压下限",
    "平均停电时间",
    "变电站", "配电站", "配电室", "箱式变电站", "低压台区",
    "配电网", "配变", "台区", "主线", "支线", "架空线路",
    "电缆", "光伏", "电能质量", "负载率", "电压偏差", "线损", "净高",
}, key=len, reverse=True))

_OBJECT_ALIASES = {
    "箱变": ("箱变", "箱式变电站"),
    "TN系统": ("TN系统", "TN-C-S", "TN-S"),
    "配变": ("配变", "配电变压器"),
    "建筑面积": ("建筑面积", "用地面积"),
    "用地面积": ("用地面积", "建筑面积"),
}

_RELATION_ALIASES = {
    "等效": ("等效", "视作", "视为", "看作"),
    "等效为": ("等效为", "视作", "视为", "看作"),
}

_FOCUS_STOP_TERMS = {
    "哪些", "什么", "多少", "如何", "怎么", "为什么", "是否", "分别",
    "根据", "指出", "研究", "提出", "可以", "通过", "实现", "相关",
    "具体", "其中", "以及", "进行", "问题", "要求", "内容", "情况",
}

_FULL_EVIDENCE_SIGNALS = (
    "多少", "分别", "数值", "比例", "比率", "百分比", "时间", "容量",
    "数量", "多大", "不低于", "不高于", "不小于", "不大于", "限值",
    "准确率", "覆盖率", "负载率",
)

_FACT_LOOKUP_SIGNALS = (
    "多少", "分别", "数值", "比例", "比率", "百分比", "时间", "容量",
    "数量", "几个", "哪几", "多大", "限值", "上限", "下限", "要求",
    "等效为", "视作什么", "视为什么", "看作什么",
)

_ALIGNMENT_STOP_TERMS = _FOCUS_STOP_TERMS | {
    "论文", "文章", "文献", "作者", "研究中", "文中", "研究", "采用",
    "达到", "发生", "对应", "给出", "说明", "指出", "要求", "进行",
}

# ── Dense retrieval (ChromaDB) ────────────────────────────────────────────────

_ef = None
_collections: dict[str, chromadb.Collection] = {}


def _resolve_device() -> str:
    if LOCAL_MODEL_DEVICE != "auto":
        return LOCAL_MODEL_DEVICE
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _get_embedding_function() -> SentenceTransformerEmbeddingFunction:
    global _ef
    if _ef is not None:
        return _ef
    _ef = SentenceTransformerEmbeddingFunction(
        model_name=EMBED_MODEL,
        device=_resolve_device(),
        normalize_embeddings=True,
    )
    return _ef


def _get_collection(collection_name: str = COLLECTION_NAME) -> chromadb.Collection:
    global _collections
    if collection_name in _collections:
        return _collections[collection_name]
    client = chromadb.PersistentClient(path=str(VECTORSTORE_DIR))
    collection = client.get_collection(name=collection_name)
    _collections[collection_name] = collection
    return collection


def initialize_retrieval(
    collection_name: str = STRUCTURED_COLLECTION_NAME,
) -> int:
    """Load the shared embedding model and return the main collection size."""
    _get_embedding_function()
    return _get_collection(collection_name).count()


def _restore_original_content(hit: dict) -> dict:
    if hit.get("original_content"):
        hit = dict(hit)
        hit["content"] = hit.pop("original_content")
    return hit


def _dense_search(
    query: str,
    top_k: int,
    doc_type: str | None,
    collection_name: str = COLLECTION_NAME,
) -> list[dict]:
    collection = _get_collection(collection_name)
    query_embedding = _get_embedding_function()([QUERY_PREFIX + query])
    where = {"doc_type": doc_type} if doc_type else None
    results = collection.query(
        query_embeddings=query_embedding,
        n_results=top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    hits = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        hits.append(_restore_original_content({"content": doc, "distance": dist, **meta}))
    return hits


# ── BM25 sparse retrieval ─────────────────────────────────────────────────────

_bm25_cache: dict[str, tuple[BM25Okapi, list[dict]]] = {}


def _get_bm25(
    chunks_file=CHUNKS_FILE,
    structured: bool = False,
) -> tuple[BM25Okapi, list[dict]]:
    key = f"{chunks_file}::structured={structured}"
    if key in _bm25_cache:
        return _bm25_cache[key]
    chunks: list[dict] = []
    with open(chunks_file, encoding="utf-8") as f:
        for line in f:
            chunks.append(json.loads(line))
    tokenized = [
        list(jieba.cut(
            build_index_text(c)
            if structured
            else c.get("indexed_content", c["content"])
        ))
        for c in chunks
    ]
    bm25 = BM25Okapi(tokenized)
    _bm25_cache[key] = (bm25, chunks)
    return bm25, chunks


def _bm25_search(
    query: str,
    top_k: int,
    doc_type: str | None,
    chunks_file=CHUNKS_FILE,
    structured: bool = False,
) -> list[dict]:
    bm25, chunks = _get_bm25(chunks_file, structured=structured)
    tokens = list(jieba.cut(query))
    scores = bm25.get_scores(tokens)
    pairs = [
        (i, float(s)) for i, s in enumerate(scores)
        if doc_type is None or chunks[i].get("doc_type") == doc_type
    ]
    pairs.sort(key=lambda x: x[1], reverse=True)
    hits = []
    for i, score in pairs[:top_k]:
        c = dict(chunks[i])
        c.pop("indexed_content", None)
        c["bm25_score"] = score
        c.setdefault("distance", 1.0)  # no cosine distance for BM25-only hits
        hits.append(c)
    return hits


# ── RRF fusion ────────────────────────────────────────────────────────────────

def _rrf_fuse(dense: list[dict], sparse: list[dict]) -> list[dict]:
    """Reciprocal Rank Fusion: score(d) = Σ 1 / (RRF_K + rank_i(d))"""
    scores: dict[str, float] = {}
    pool: dict[str, dict] = {}

    def cid(h: dict) -> str:
        return f"{h['source']}::{h['chunk_idx']}"

    for rank, h in enumerate(dense):
        c = cid(h)
        scores[c] = scores.get(c, 0.0) + 1.0 / (RRF_K + rank + 1)
        pool[c] = h

    for rank, h in enumerate(sparse):
        c = cid(h)
        scores[c] = scores.get(c, 0.0) + 1.0 / (RRF_K + rank + 1)
        if c in pool:
            pool[c] = {**pool[c], "bm25_score": h.get("bm25_score", 0.0)}
        else:
            pool[c] = h

    return [
        {**pool[c], "rrf_score": scores[c]}
        for c in sorted(scores, key=scores.__getitem__, reverse=True)
    ]


# ── Reranker (Cross-encoder) ──────────────────────────────────────────────────

_reranker = None


def _get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder(RERANK_MODEL, max_length=512, device=_resolve_device())
    return _reranker


def _query_object_terms(query: str) -> list[str]:
    """Return non-nested domain objects explicitly named in the query."""
    terms = []
    for term in _QUERY_OBJECT_TERMS:
        if term in query and not any(term in longer for longer in terms):
            terms.append(term)
    for rating in re.findall(r"\d+(?:\.\d+)?\s*(?:kV|V|kVA|MVA)", query, re.I):
        normalized = re.sub(r"\s+", "", rating)
        if normalized not in terms:
            terms.append(normalized)
    return terms


def _section_rerank_text(chunk: dict, body: str | None = None) -> str:
    """Represent a section without repeating document-level title metadata."""
    parts = []
    headings = heading_path(chunk)
    if headings:
        parts.append(f"章节路径：{headings}")
    parts.append(
        "正文：" + str(
            body
            if body is not None
            else chunk.get("rerank_content", chunk.get("content", ""))
        ).strip()
    )
    return "\n".join(parts)


def _object_match_score(
    query: str,
    chunk: dict,
    section_only: bool = False,
) -> float:
    """Measure whether a candidate discusses the concrete queried objects."""
    terms = _query_object_terms(query)
    if not terms:
        return 0.0
    body = str(chunk.get(
        "body_content",
        chunk.get("rerank_content", chunk.get("content", "")),
    ))
    text = (
        _section_rerank_text(chunk, body)
        if section_only
        else build_index_text({**chunk, "content": body})
    )
    normalized_text = _normalized_text(text)

    def matches(term: str) -> bool:
        variants = _OBJECT_ALIASES.get(term, (term,))
        for variant in variants:
            normalized_variant = _normalized_text(variant)
            if normalized_variant in normalized_text:
                return True
        rating = re.fullmatch(r"(\d+(?:\.\d+)?)(kv|v|kva|mva)", term, re.I)
        if rating:
            number, unit = rating.groups()
            return number in normalized_text and unit.lower() in normalized_text
        return False

    return sum(matches(term) for term in terms) / len(terms)


def _keyword_match_score(
    query: str,
    chunk: dict,
    section_only: bool = False,
) -> float:
    """Measure weighted query-keyword coverage in a candidate section."""
    weighted_terms = jieba.analyse.extract_tags(query, topK=8, withWeight=True)
    if not weighted_terms:
        return 0.0
    body = str(chunk.get("rerank_content", chunk.get("content", "")))
    text = (
        _section_rerank_text(chunk, body)
        if section_only
        else build_index_text({**chunk, "content": body})
    )
    total = sum(weight for _, weight in weighted_terms)
    return sum(weight for term, weight in weighted_terms if term in text) / total


def _is_fact_lookup(query: str) -> bool:
    return any(signal in query for signal in _FACT_LOOKUP_SIGNALS)


def _query_alignment_terms(query: str) -> list[tuple[str, float]]:
    """Return answer-bearing query terms for local evidence matching."""
    weighted: dict[str, tuple[str, float]] = {}

    def add(term: str, weight: float) -> None:
        term = term.strip()
        normalized = _normalized_text(term)
        if not normalized or term in _ALIGNMENT_STOP_TERMS:
            return
        if len(normalized) < 2 and not re.search(r"\d|[A-Za-z]", normalized):
            return
        current = weighted.get(normalized)
        if current is None or weight > current[1]:
            weighted[normalized] = (term, weight)

    for term, weight in jieba.analyse.extract_tags(query, topK=14, withWeight=True):
        add(term, float(weight))
    for term in _query_object_terms(query):
        add(term, 1.8)
    for token in re.findall(
        r"(?:[A-Fa-f]\+?\s*类|\d+(?:\.\d+)?\s*(?:kV|V|kVA|MVA|kW|MW|%))",
        query,
    ):
        add(token, 2.0)

    # Jieba often separates relation labels such as "负荷 密度" or
    # "线路 电阻". Preserve adjacent compounds because table headers use them.
    tokens = [
        token.strip()
        for token in jieba.lcut(query)
        if token.strip()
        and re.search(r"[\w一-鿿]", token)
        and token.strip() not in _ALIGNMENT_STOP_TERMS
        and token.strip() not in {"的", "和", "与", "及", "中", "为", "是"}
    ]
    for left, right in zip(tokens, tokens[1:]):
        compound = left + right
        if 4 <= len(_normalized_text(compound)) <= 12:
            add(compound, 2.2)
    return list(weighted.values())


def _term_matches_text(term: str, text: str) -> bool:
    normalized_text = _normalized_text(text)
    variants = _RELATION_ALIASES.get(term, _OBJECT_ALIASES.get(term, (term,)))
    return any(
        _normalized_text(variant) in normalized_text
        for variant in variants
        if _normalized_text(variant)
    )


def _query_subqueries(query: str) -> list[str]:
    """Split multi-part questions for document-constrained section retrieval."""
    parts: list[str] = []
    for sentence in re.split(r"[？?；;]+", query):
        clauses = re.split(
            r"[，,]\s*(?=(?:其中|并且|同时|以及|此外|该(?:方法|指标|系统|值)|"
            r"其(?:中|优点|缺点)|计算|判断))",
            sentence,
        )
        for clause in clauses:
            clause = clause.strip(" ，,。；;？?")
            if len(_normalized_text(clause)) < 6:
                continue
            if clause not in parts:
                parts.append(clause)
    return parts[:3]


def _evidence_units(body: str) -> list[str]:
    """Split text into sentences while preserving complete Markdown tables."""
    units: list[str] = []
    prose_lines: list[str] = []
    table_lines: list[str] = []

    def flush_prose() -> None:
        if not prose_lines:
            return
        paragraph = re.sub(r"\s*\n\s*", " ", "\n".join(prose_lines)).strip()
        prose_lines.clear()
        if not paragraph:
            return
        if re.match(r"^[A-Za-z0-9][)）]", paragraph):
            units.append(paragraph)
            return
        parts = re.split(r"(?<=[。！？；])\s*", paragraph)
        units.extend(part.strip() for part in parts if len(part.strip()) >= 6)

    def flush_table() -> None:
        if not table_lines:
            return
        table = "\n".join(table_lines).strip()
        table_lines.clear()
        if table:
            units.append(table)

    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            flush_prose()
            table_lines.append(stripped)
        else:
            flush_table()
            if stripped:
                if re.match(r"^[A-Za-z0-9][)）]", stripped):
                    flush_prose()
                prose_lines.append(stripped)
            else:
                flush_prose()
    flush_table()
    flush_prose()
    return units


def _looks_like_toc(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if "目录" in compact or "目次" in compact:
        return True
    chapter_count = len(re.findall(r"第(?:\d+|[一二三四五六七八九十]+)章", compact))
    return chapter_count >= 3


def _novel_number_count(query: str, text: str) -> int:
    query_numbers = set(re.findall(r"\d+(?:\.\d+)?", query))
    values = re.findall(r"\d+(?:\.\d+)?", text)
    return len({value for value in values if value not in query_numbers})


def _unit_alignment_score(
    query: str,
    unit: str,
    weighted_terms: list[tuple[str, float]],
) -> float:
    if not weighted_terms:
        return 0.0
    total_weight = sum(weight for _, weight in weighted_terms)
    matched = [
        (term, weight)
        for term, weight in weighted_terms
        if _term_matches_text(term, unit)
    ]
    coverage = sum(weight for _, weight in matched) / total_weight
    concentration = min(1.0, len(matched) / min(5, len(weighted_terms)))
    score = 0.70 * coverage + 0.30 * concentration

    if _is_fact_lookup(query):
        novel_numbers = _novel_number_count(query, unit)
        if novel_numbers >= 2:
            score += 0.30
        elif novel_numbers == 1:
            score += 0.18
        elif any(signal in query for signal in ("多少", "分别", "数值", "比例")):
            score *= 0.65
    if unit.count("|") >= 6 and matched:
        score += 0.12
    return min(1.0, score)


def _fact_alignment_score(query: str, chunk: dict) -> float:
    """Score whether one local span jointly contains the requested fact labels and values."""
    if not _is_fact_lookup(query):
        return 0.0
    body = str(chunk.get(
        "body_content",
        chunk.get("rerank_content", chunk.get("content", "")),
    ))
    if not body or _looks_like_toc(body):
        return 0.0
    terms = _query_alignment_terms(query)
    heading = heading_path(chunk)
    return max(
        (
            _unit_alignment_score(query, f"{heading}\n{unit}", terms)
            for unit in _evidence_units(body)
        ),
        default=0.0,
    )


def _answer_shape_score(query: str, chunk: dict) -> float:
    """Reward evidence whose syntax matches the requested answer shape."""
    body = str(chunk.get(
        "body_content",
        chunk.get("rerank_content", chunk.get("content", "")),
    ))
    relation_query = bool(re.search(
        r"(?:等效|视作|视为|看作).{0,8}(?:什么|何种|为何)",
        query,
    ))
    if relation_query and re.search(r"(?:等效为|视作|视为|看作)", body):
        return 1.0
    enumeration_query = bool(re.search(
        r"哪些|哪几|共(?:细分为)?多少|多少(?:个|项|种|类)|包含什么|包括什么",
        query,
    ))
    if enumeration_query:
        introduces_list = bool(re.search(
            r"包括|包含|分为|分别为|细分为|主要有|可分成|由.+组成",
            body,
        ))
        has_multiple_items = (
            body.count("、") >= 1
            or bool(re.search(r"[;；].+[;；]", body))
            or bool(re.search(r"包括.{2,80}(?:以及|和|与).{2,40}", body))
        )
        if introduces_list and has_multiple_items:
            return 1.0
        if introduces_list:
            return 0.5
    table_rows = sum(
        line.lstrip().startswith("|")
        for line in body.splitlines()
    )
    if table_rows >= 2 and any(signal in query for signal in (
        "多少", "分别", "哪几", "哪些", "限值", "上限", "下限", "不低于",
    )):
        return 1.0
    if any(signal in query for signal in ("多少", "分别", "上限", "下限")):
        if len(re.findall(r"\d+(?:\.\d+)?", body)) >= 2:
            return 0.5
    return 0.0


def rerank(
    query: str,
    chunks: list[dict],
    top_n: int,
    structured: bool = False,
    section_only: bool = False,
) -> list[dict]:
    """Re-rank *chunks* for *query* using a cross-encoder. Returns top_n results."""
    if not chunks:
        return []
    reranker = _get_reranker()

    def candidate_text(chunk: dict) -> str:
        body = str(chunk.get("rerank_content", chunk.get("content", "")))
        focused = _query_focused_excerpt(query, body)
        return focused or body

    scores = reranker.predict([
        (
            query,
            (
                _section_rerank_text(c, candidate_text(c))
                if section_only
                else build_index_text({**c, "content": candidate_text(c)})
            ) if structured else candidate_text(c),
        )
        for c in chunks
    ])
    scored = [
        {**chunk, "rerank_score": float(score)}
        for score, chunk in zip(scores, chunks)
    ]
    if structured:
        normalized_scores = _minmax([row["rerank_score"] for row in scored])
        lexical_scores = _minmax([
            max(0.0, float(row.get("bm25_score", 0.0)))
            for row in scored
        ])
        metadata_scores = [metadata_match_score(query, row) for row in scored]
        object_scores = [
            _object_match_score(query, row, section_only=section_only)
            for row in scored
        ]
        keyword_scores = [
            _keyword_match_score(query, row, section_only=section_only)
            for row in scored
        ]
        answer_shape_scores = [_answer_shape_score(query, row) for row in scored]
        fact_alignment_scores = [_fact_alignment_score(query, row) for row in scored]
        has_explicit_author_match = any(score >= 1.0 for score in metadata_scores)
        query_objects = _query_object_terms(query)
        object_gate_threshold = (
            1.0
            if len(query_objects) <= 2
            else (len(query_objects) - 1) / len(query_objects)
        )
        hard_object_constraint = (
            len(query_objects) >= 2
            and max(object_scores, default=0.0) >= object_gate_threshold
        )
        fact_lookup = _is_fact_lookup(query)
        for row, normalized_score, lexical_score, metadata_score, object_score, keyword_score, answer_shape_score, fact_alignment_score in zip(
            scored,
            normalized_scores,
            lexical_scores,
            metadata_scores,
            object_scores,
            keyword_scores,
            answer_shape_scores,
            fact_alignment_scores,
        ):
            row["metadata_match_score"] = metadata_score
            row["lexical_score"] = lexical_score
            row["object_match_score"] = object_score
            row["keyword_match_score"] = keyword_score
            row["answer_shape_score"] = answer_shape_score
            row["fact_alignment_score"] = fact_alignment_score
            window_bonus = (
                1.0
                if fact_lookup and str(row.get("chunk_idx", "")).startswith("window:")
                else 0.0
            )
            object_gate = (
                1.0
                if hard_object_constraint and object_score >= object_gate_threshold
                else 0.0
            )
            region_gate = 0.6 if 0.75 <= metadata_score < 1.0 else 0.0
            if has_explicit_author_match:
                # An author explicitly named by the user is a document-level
                # constraint, not a weak semantic hint.
                if fact_lookup:
                    row["structured_score"] = (
                        (1.0 if metadata_score >= 1.0 else 0.0)
                        + object_gate
                        + region_gate
                        + 0.25 * normalized_score
                        + 0.24 * lexical_score
                        + 0.16 * object_score
                        + 0.25 * keyword_score
                        + 0.10 * window_bonus
                        + 0.15 * answer_shape_score
                        + 0.85 * fact_alignment_score
                    )
                else:
                    row["structured_score"] = (
                        (1.0 if metadata_score >= 1.0 else 0.0)
                        + object_gate
                        + region_gate
                        + 0.52 * normalized_score
                        + 0.13 * lexical_score
                        + 0.15 * object_score
                        + 0.20 * keyword_score
                        + 0.12 * answer_shape_score
                    )
            elif fact_lookup:
                row["structured_score"] = (
                    object_gate
                    + region_gate
                    + 0.43 * normalized_score
                    + 0.16 * lexical_score
                    + 0.27 * object_score
                    + 0.06 * keyword_score
                    + 0.03 * metadata_score
                    + 0.05 * window_bonus
                    + 0.15 * answer_shape_score
                    + 0.85 * fact_alignment_score
                )
            else:
                row["structured_score"] = (
                    object_gate
                    + region_gate
                    + 0.67 * normalized_score
                    + 0.16 * object_score
                    + 0.10 * keyword_score
                    + 0.07 * metadata_score
                    + 0.12 * answer_shape_score
                )
        scored.sort(key=lambda row: row["structured_score"], reverse=True)
    else:
        scored.sort(key=lambda row: row["rerank_score"], reverse=True)
    return scored[:top_n]


def _dedupe_by_source(chunks: list[dict], max_per: int) -> list[dict]:
    """Keep at most *max_per* chunks per source document, preserving rank order."""
    counts: dict[str, int] = {}
    out = []
    for c in chunks:
        src = c["source"]
        if counts.get(src, 0) < max_per:
            counts[src] = counts.get(src, 0) + 1
            out.append(c)
    return out


def _minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    low, high = min(values), max(values)
    if high - low < 1e-12:
        return [1.0] * len(values)
    return [(value - low) / (high - low) for value in values]


def _rank_score(chunk: dict) -> float:
    if "structured_score" in chunk:
        return float(chunk["structured_score"])
    if "rerank_score" in chunk:
        return float(chunk["rerank_score"])
    if "rrf_score" in chunk:
        return float(chunk["rrf_score"])
    if "bm25_score" in chunk:
        return float(chunk["bm25_score"])
    return -float(chunk.get("distance", 1.0))


def _aggregate_group_scores(
    chunks: list[dict],
    normalized_chunk_scores: list[float],
    key_fn,
    support_weights: tuple[float, ...],
) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for chunk, score in zip(chunks, normalized_chunk_scores):
        grouped[key_fn(chunk)].append(score)

    raw_scores = {}
    for key, scores in grouped.items():
        ranked = sorted(scores, reverse=True)
        raw_scores[key] = sum(
            weight * score
            for weight, score in zip(support_weights, ranked)
        )
    normalized = _minmax(list(raw_scores.values()))
    return {
        key: score
        for key, score in zip(raw_scores, normalized)
    }


def _normalized_text(text: str) -> str:
    return re.sub(r"[^\w]+", "", text.lower(), flags=re.UNICODE)


def _char_ngrams(text: str, size: int = 4) -> set[str]:
    normalized = _normalized_text(text)
    if len(normalized) <= size:
        return {normalized} if normalized else set()
    return {
        normalized[index:index + size]
        for index in range(len(normalized) - size + 1)
    }


def _near_duplicate(left: str, right: str, threshold: float = 0.82) -> bool:
    left_grams = _char_ngrams(left)
    right_grams = _char_ngrams(right)
    if not left_grams or not right_grams:
        return False
    intersection = len(left_grams & right_grams)
    jaccard = intersection / len(left_grams | right_grams)
    containment = intersection / min(len(left_grams), len(right_grams))
    return jaccard >= threshold or containment >= 0.90


def _chunk_index_set(chunk: dict) -> set[int]:
    indices = chunk.get("window_chunk_indices")
    if isinstance(indices, list):
        return {index for index in indices if isinstance(index, int)}
    chunk_idx = chunk.get("chunk_idx")
    if isinstance(chunk_idx, int):
        return {chunk_idx}
    if isinstance(chunk_idx, str):
        match = re.fullmatch(r"window:(\d+)-(\d+)", chunk_idx)
        if match:
            return {int(match.group(1)), int(match.group(2))}
    return set()


def _overlapping_evidence(left: dict, right: dict) -> bool:
    """Treat overlapping parent/child windows from one document as duplicates."""
    if left.get("source") != right.get("source"):
        return False
    left_indices = _chunk_index_set(left)
    right_indices = _chunk_index_set(right)
    return bool(left_indices and right_indices and left_indices & right_indices)


def _query_author_sources(query: str, chunks_file=CHUNKS_FILE) -> list[str]:
    """Return corpus sources whose filename author is explicitly in the query."""
    _, chunks = _get_bm25(chunks_file, structured=True)
    matches = []
    seen = set()
    for chunk in chunks:
        source = str(chunk.get("source", ""))
        author = source_author(source)
        if author and author in query and source not in seen:
            seen.add(source)
            matches.append(source)
    return matches


def _merge_adjacent_content(left: str, right: str) -> str:
    """Join adjacent chunks without preserving OCR splits inside a word."""
    left = left.rstrip()
    right = right.lstrip()
    if not left:
        return right
    if not right:
        return left
    if left.endswith(("。", "！", "？", "；", ".", "!", "?", ";", "|")):
        separator = "\n\n"
    elif right.startswith(("#", "|", "- ", "* ", "> ")):
        separator = "\n\n"
    elif left[-1].isascii() and right[0].isascii():
        separator = " "
    else:
        separator = ""
    return f"{left}{separator}{right}"


def _source_sparse_candidates(
    query: str,
    sources: list[str],
    per_source: int = 20,
    chunks_file=CHUNKS_FILE,
) -> list[dict]:
    """Find additional candidate sections inside already selected documents."""
    if not sources:
        return []
    bm25, chunks = _get_bm25(chunks_file, structured=True)
    scores = bm25.get_scores(list(jieba.cut(query)))
    source_set = set(sources)
    grouped: dict[str, list[tuple[float, int]]] = defaultdict(list)
    chunk_lookup = {
        (str(chunk.get("source", "")), chunk.get("chunk_idx")): index
        for index, chunk in enumerate(chunks)
    }
    for index, score in enumerate(scores):
        source = str(chunks[index].get("source", ""))
        if source in source_set:
            grouped[source].append((float(score), index))

    candidates = []
    for source in sources:
        ranked = sorted(grouped[source], reverse=True)[:per_source]
        candidate_indices = {index for _, index in ranked}
        for _, index in ranked:
            chunk_idx = chunks[index].get("chunk_idx")
            if not isinstance(chunk_idx, int):
                continue
            for neighbor_idx in (chunk_idx - 1, chunk_idx + 1):
                neighbor = chunk_lookup.get((source, neighbor_idx))
                if neighbor is not None:
                    candidate_indices.add(neighbor)
        for index in sorted(candidate_indices):
            chunk = dict(chunks[index])
            chunk.pop("indexed_content", None)
            chunk["bm25_score"] = float(scores[index])
            chunk.setdefault("distance", 1.0)
            candidates.append(chunk)

        # OCR text and tables are often split exactly at the answer boundary.
        # Keep small chunks for matching, then rerank adjacent two-chunk windows.
        window_pairs = set()
        for _, index in ranked:
            chunk_idx = chunks[index].get("chunk_idx")
            if not isinstance(chunk_idx, int):
                continue
            for left_idx, right_idx in (
                (chunk_idx - 1, chunk_idx),
                (chunk_idx, chunk_idx + 1),
            ):
                left = chunk_lookup.get((source, left_idx))
                right = chunk_lookup.get((source, right_idx))
                if left is not None and right is not None:
                    window_pairs.add((left, right))
        for left, right in sorted(window_pairs):
            left_chunk = chunks[left]
            right_chunk = chunks[right]
            window = dict(left_chunk)
            left_body = str(
                left_chunk.get("body_content", left_chunk.get("content", ""))
            ).strip()
            right_body = str(
                right_chunk.get("body_content", right_chunk.get("content", ""))
            ).strip()
            merged_body = _merge_adjacent_content(left_body, right_body)
            window["content"] = merged_body
            window["body_content"] = merged_body
            window["rerank_content"] = _merge_adjacent_content(
                left_body[-450:], right_body[:550],
            )
            window["chunk_idx"] = (
                f"window:{left_chunk.get('chunk_idx')}-{right_chunk.get('chunk_idx')}"
            )
            window["window_chunk_indices"] = [
                left_chunk.get("chunk_idx"), right_chunk.get("chunk_idx"),
            ]
            window["bm25_score"] = max(float(scores[left]), float(scores[right]))
            window.setdefault("distance", 1.0)
            candidates.append(window)
    return candidates


def _source_dense_candidates(
    query: str,
    sources: list[str],
    per_source: int = 20,
    collection_name: str = STRUCTURED_COLLECTION_NAME,
    require_multiple_subqueries: bool = True,
) -> list[dict]:
    """Retrieve each sub-question again inside the already identified document."""
    subqueries = _query_subqueries(query)
    if not sources or (require_multiple_subqueries and len(subqueries) < 2):
        return []
    if not subqueries:
        subqueries = [query]

    query_embeddings = _get_embedding_function()([
        QUERY_PREFIX + subquery for subquery in subqueries
    ])
    collection = _get_collection(collection_name)
    candidates: list[dict] = []
    seen: set[tuple[str, object]] = set()
    for source in sources:
        results = collection.query(
            query_embeddings=query_embeddings,
            n_results=per_source,
            where={"source": source},
            include=["documents", "metadatas", "distances"],
        )
        for subquery, documents, metadatas, distances in zip(
            subqueries,
            results["documents"],
            results["metadatas"],
            results["distances"],
        ):
            for document, metadata, distance in zip(documents, metadatas, distances):
                row = _restore_original_content({
                    "content": document,
                    "distance": float(distance),
                    **metadata,
                })
                identity = (str(row.get("source", "")), row.get("chunk_idx"))
                if identity in seen:
                    continue
                seen.add(identity)
                row["matched_subquery"] = subquery
                candidates.append(row)
    return candidates


def _expand_document_candidates(
    query: str,
    reranked: list[dict],
    chunks_file=CHUNKS_FILE,
    collection_name: str = STRUCTURED_COLLECTION_NAME,
) -> list[dict]:
    """Add high-scoring sections from likely documents before final selection."""
    author_sources = _query_author_sources(query, chunks_file=chunks_file)
    if author_sources:
        sources = author_sources[:1]
    else:
        sources = []
        for chunk in reranked:
            source = str(chunk.get("source", ""))
            if source and source not in sources:
                sources.append(source)
            if len(sources) == 3:
                break

    expanded = list(reranked)
    seen = {
        (str(chunk.get("source", "")), chunk.get("chunk_idx"))
        for chunk in expanded
    }
    for chunk in _source_sparse_candidates(
        query, sources, per_source=20, chunks_file=chunks_file,
    ):
        identity = (str(chunk.get("source", "")), chunk.get("chunk_idx"))
        if identity not in seen:
            seen.add(identity)
            expanded.append(chunk)
    dense_sources = sources if author_sources else sources[:1]
    for chunk in _source_dense_candidates(
        query,
        dense_sources,
        per_source=20,
        collection_name=collection_name,
    ):
        identity = (str(chunk.get("source", "")), chunk.get("chunk_idx"))
        if identity not in seen:
            seen.add(identity)
            expanded.append(chunk)
    return expanded


def _query_focused_excerpt(query: str, body: str, limit: int = 3) -> str:
    """Extract query-aligned original spans while keeping tables intact."""
    if not query or not body:
        return ""
    weighted_terms = _query_alignment_terms(query)
    if not weighted_terms:
        return ""

    units = _evidence_units(body)
    ranked = []
    fact_lookup = _is_fact_lookup(query)
    for index, unit in enumerate(units):
        if fact_lookup:
            score = _unit_alignment_score(query, unit, weighted_terms)
        else:
            score = sum(
                weight for term, weight in weighted_terms
                if _term_matches_text(term, unit)
            )
        if score <= 0:
            continue
        ranked.append((score, index, unit))
    ranked.sort(key=lambda item: (-item[0], item[1]))

    selected = []
    target_limit = max(limit, 4) if fact_lookup else limit
    for _, _, unit in ranked:
        if any(_near_duplicate(unit, kept, threshold=0.75) for kept in selected):
            continue
        selected.append(unit)
        if len(selected) == target_limit:
            break
    formatted = []
    for unit in selected:
        if unit.lstrip().startswith("|"):
            formatted.append(unit)
        else:
            formatted.append(f"- {unit}")
    return "\n\n".join(formatted)


def _requires_full_evidence(query: str) -> bool:
    """Keep parent text for exact-value, table and fixed-enumeration queries."""
    if any(signal in query for signal in _FULL_EVIDENCE_SIGNALS):
        return True
    return bool(re.search(r"哪[一二三四五六七八九十\d]+(?:种|项|个|类)", query))


def _with_structured_context(chunks: list[dict], query: str = "") -> list[dict]:
    """Return self-contained, extractively compressed evidence contexts."""
    enriched = []
    for chunk in chunks:
        body = str(chunk.get("body_content", chunk.get("content", ""))).strip()
        row = {**chunk, "body_content": body}
        excerpt = _query_focused_excerpt(query, body)
        if excerpt:
            context = build_index_text({**row, "content": excerpt})
            context = context.replace(
                "\n正文：",
                "\n可直接用于回答问题的原文证据：\n",
                1,
            )
        else:
            context = build_index_text({**row, "content": body})
        row["content"] = context
        row["context_format"] = "structured"
        enriched.append(row)
    return enriched


def _hierarchical_select(
    chunks: list[dict],
    top_k: int,
    primary_max: int = 4,
) -> list[dict]:
    """Rank document -> section -> chunk, then assemble complementary evidence."""
    if not chunks or top_k <= 0:
        return []

    chunk_scores = _minmax([_rank_score(chunk) for chunk in chunks])
    document_scores = _aggregate_group_scores(
        chunks,
        chunk_scores,
        key_fn=lambda chunk: str(chunk.get("source", "")),
        support_weights=(1.0, 0.18, 0.08),
    )
    section_scores = _aggregate_group_scores(
        chunks,
        chunk_scores,
        key_fn=section_key,
        support_weights=(1.0, 0.12),
    )

    ranked = []
    for chunk, chunk_score in zip(chunks, chunk_scores):
        document_score = document_scores[str(chunk.get("source", ""))]
        section_score = section_scores[section_key(chunk)]
        hierarchical_score = (
            0.68 * chunk_score
            + 0.22 * document_score
            + 0.10 * section_score
        )
        ranked.append({
            **chunk,
            "document_score": document_score,
            "section_score": section_score,
            "hierarchical_score": hierarchical_score,
        })
    ranked.sort(key=lambda chunk: chunk["hierarchical_score"], reverse=True)

    # The strongest child chunk remains the retrieval anchor. Aggregate scores
    # organize the remaining evidence but cannot replace a confident Top-1 hit.
    anchor = chunks[0]
    anchor_id = (anchor.get("source"), anchor.get("chunk_idx"))
    ranked.sort(
        key=lambda chunk: (
            chunk.get("source"), chunk.get("chunk_idx")
        ) != anchor_id
    )

    document_order = sorted(
        document_scores,
        key=document_scores.__getitem__,
        reverse=True,
    )
    primary_source = str(anchor.get("source", ""))
    top_chunk_by_source: dict[str, float] = defaultdict(float)
    for chunk, score in zip(chunks, chunk_scores):
        source = str(chunk.get("source", ""))
        top_chunk_by_source[source] = max(top_chunk_by_source[source], score)
    competing_top_score = max(
        (score for source, score in top_chunk_by_source.items() if source != primary_source),
        default=0.0,
    )
    margin = top_chunk_by_source[primary_source] - competing_top_score
    primary_support = sum(
        score >= 0.65
        for chunk, score in zip(chunks, chunk_scores)
        if chunk.get("source") == primary_source
    )
    if margin >= 0.20 and primary_support >= 2:
        primary_limit = min(primary_max, top_k)
    elif margin >= 0.08 or (margin >= 0.04 and primary_support >= 2):
        primary_limit = min(3, top_k)
    else:
        primary_limit = min(2, top_k)

    source_limits = defaultdict(lambda: 2)
    source_limits[primary_source] = primary_limit
    selected: list[dict] = []
    source_counts: dict[str, int] = defaultdict(int)
    section_counts: dict[str, int] = defaultdict(int)

    def try_select(chunk: dict, enforce_section_limit: bool) -> bool:
        source = str(chunk.get("source", ""))
        section = section_key(chunk)
        if source_counts[source] >= source_limits[source]:
            return False
        section_limit = 3 if source == primary_source else 2
        if enforce_section_limit and section_counts[section] >= section_limit:
            return False
        if any(_overlapping_evidence(chunk, kept) for kept in selected):
            return False
        chunk_body = str(chunk.get("body_content", chunk.get("content", "")))
        if any(
            _near_duplicate(
                chunk_body,
                str(kept.get("body_content", kept.get("content", ""))),
            )
            for kept in selected
        ):
            return False
        selected.append(chunk)
        source_counts[source] += 1
        section_counts[section] += 1
        return True

    for enforce_section_limit in (True, False):
        for chunk in ranked:
            if chunk in selected:
                continue
            try_select(chunk, enforce_section_limit)
            if len(selected) == top_k:
                return selected
    return selected


# ── Public interface ──────────────────────────────────────────────────────────

def retrieve(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    doc_type: str | None = None,
    mode: str = DEFAULT_RETRIEVAL_MODE,
    max_per_source: int = 2,
) -> list[dict]:
    """
    Return top_k chunks most relevant to *query*.

    Each result dict contains: content, source, doc_type, chunk_idx,
    distance (cosine, dense hits), and mode-specific scores
    (bm25_score, rrf_score, rerank_score).

    Args:
        query:          Natural language question.
        top_k:          Number of results to return.
        doc_type:       Optional filter — "regulation", "journal", or "thesis".
        mode:           Base mode, optionally suffixed with "+structured" or
                        "+hierarchical".
        max_per_source: Max chunks per source doc in final results (default 2).
    """
    use_hierarchical = mode.endswith("+hierarchical")
    use_structured = use_hierarchical or mode.endswith("+structured")
    if use_hierarchical:
        base_mode = mode.removesuffix("+hierarchical")
    elif use_structured:
        base_mode = mode.removesuffix("+structured")
    else:
        base_mode = mode
    use_doc2query = base_mode.endswith("+doc2query")
    base_mode = base_mode.removesuffix("+doc2query")
    if use_doc2query and use_structured:
        raise ValueError("doc2query cannot be combined with structured/hierarchical yet")
    if use_structured:
        collection_name = STRUCTURED_COLLECTION_NAME
    elif use_doc2query:
        collection_name = DOC2QUERY_COLLECTION_NAME
    else:
        collection_name = COLLECTION_NAME
    chunks_file = DOC2QUERY_CHUNKS_FILE if use_doc2query else CHUNKS_FILE

    if base_mode == "dense":
        hits = _dense_search(query, top_k, doc_type, collection_name=collection_name)
        return _with_structured_context(hits, query) if use_structured else hits

    fetch_k = max(top_k * 3, 20)
    dense_hits  = _dense_search(query, fetch_k, doc_type, collection_name=collection_name)
    sparse_hits = _bm25_search(
        query,
        fetch_k,
        doc_type,
        chunks_file=chunks_file,
        structured=use_structured,
    )
    fused = _rrf_fuse(dense_hits, sparse_hits)

    if base_mode == "hybrid":
        if use_hierarchical:
            selected = _hierarchical_select(fused, top_k)
        else:
            selected = _dedupe_by_source(fused, max_per_source)[:top_k]
        return _with_structured_context(selected, query) if use_structured else selected

    # hybrid+rerank: rerank a larger pool, then apply source diversity
    if base_mode != "hybrid+rerank":
        raise ValueError(
            "mode must be one of: dense, hybrid, hybrid+rerank, "
            "dense+doc2query, hybrid+doc2query, hybrid+rerank+doc2query, "
            "or a base mode suffixed with +structured/+hierarchical"
        )
    reranked = rerank(
        query,
        fused[:fetch_k],
        top_n=fetch_k if use_hierarchical else top_k * 3,
        structured=use_structured,
    )
    if use_hierarchical:
        author_sources = set(_query_author_sources(query, chunks_file=chunks_file))
        expanded = _expand_document_candidates(
            query,
            reranked,
            chunks_file=chunks_file,
            collection_name=collection_name,
        )
        if len(expanded) > len(reranked):
            reranked = rerank(
                query,
                expanded,
                top_n=len(expanded),
                structured=True,
                section_only=bool(author_sources),
            )
        if author_sources:
            author_chunks = [
                chunk for chunk in reranked
                if str(chunk.get("source", "")) in author_sources
            ]
            selected = _hierarchical_select(
                author_chunks,
                top_k,
                primary_max=top_k,
            )
        else:
            selected = _hierarchical_select(reranked, top_k)
    else:
        selected = _dedupe_by_source(reranked, max_per_source)[:top_k]
    return _with_structured_context(selected, query) if use_structured else selected


def retrieve_document_sections(
    query: str,
    source: str,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict]:
    """Search and rerank sections inside one exact source document."""
    if not source.strip():
        raise ValueError("source is required for document-scoped retrieval")
    if top_k <= 0:
        return []

    sparse = _source_sparse_candidates(
        query,
        [source],
        per_source=max(top_k * 5, 20),
        chunks_file=CHUNKS_FILE,
    )
    dense = _source_dense_candidates(
        query,
        [source],
        per_source=max(top_k * 5, 20),
        collection_name=STRUCTURED_COLLECTION_NAME,
        require_multiple_subqueries=False,
    )
    if not sparse and not dense:
        return []

    candidates: list[dict] = []
    seen: set[tuple[str, object]] = set()
    for hit in sparse + dense:
        identity = (str(hit.get("source", "")), hit.get("chunk_idx"))
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append(hit)
    reranked = rerank(
        query,
        candidates,
        top_n=min(len(candidates), max(top_k * 4, 12)),
        structured=True,
        section_only=True,
    )
    selected = _hierarchical_select(reranked, top_k, primary_max=top_k)
    return _with_structured_context(selected, query)


def _restore_case_fields(hit: dict) -> dict:
    """Deserialize list-valued case metadata stored by Chroma."""
    hit = dict(hit)
    for field in ("observations",):
        value = hit.get(field)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                hit[field] = parsed
    return hit


def retrieve_cases(
    query: str,
    top_k: int = 3,
    mode: str = "hybrid+rerank",
    exclude_case_ids: set[str] | None = None,
) -> list[dict]:
    """Retrieve cases separately, optionally excluding evaluation holdouts."""
    if mode.endswith("+doc2query"):
        raise ValueError("case retrieval does not support doc2query mode")
    if mode not in {"dense", "hybrid", "hybrid+rerank"}:
        raise ValueError("case mode must be one of: dense, hybrid, hybrid+rerank")

    excluded = exclude_case_ids or set()

    if mode == "dense":
        collection_count = _get_collection(CASE_COLLECTION_NAME).count()
        fetch_k = min(top_k + len(excluded), collection_count)
        hits = _dense_search(
            query, fetch_k, None, collection_name=CASE_COLLECTION_NAME,
        )
        hits = [hit for hit in hits if hit.get("case_id") not in excluded]
        return [_restore_case_fields(hit) for hit in hits[:top_k]]

    fetch_k = max(top_k * 3, 20)
    dense_hits = _dense_search(
        query, fetch_k, None, collection_name=CASE_COLLECTION_NAME,
    )
    sparse_hits = _bm25_search(query, fetch_k, None, chunks_file=CASES_FILE)
    fused = _rrf_fuse(dense_hits, sparse_hits)
    fused = [hit for hit in fused if hit.get("case_id") not in excluded]

    if mode == "hybrid":
        return [_restore_case_fields(hit) for hit in fused[:top_k]]

    reranked = rerank(query, fused[:fetch_k], top_n=top_k)
    return [_restore_case_fields(hit) for hit in reranked]


def retrieve_with_cases(
    query: str,
    knowledge_top_k: int = DEFAULT_TOP_K,
    case_top_k: int = 3,
    doc_type: str | None = None,
    mode: str = DEFAULT_RETRIEVAL_MODE,
    exclude_case_ids: set[str] | None = None,
) -> dict[str, list[dict]]:
    """Query the main knowledge base and case collection as separate evidence lanes."""
    knowledge = [
        {**hit, "evidence_type": "knowledge"}
        for hit in retrieve(
            query,
            top_k=knowledge_top_k,
            doc_type=doc_type,
            mode=mode,
        )
    ]
    case_mode = "hybrid+rerank" if mode.endswith(("+structured", "+hierarchical")) else mode
    cases = [
        {**hit, "evidence_type": "case"}
        for hit in retrieve_cases(
            query,
            top_k=case_top_k,
            mode=case_mode,
            exclude_case_ids=exclude_case_ids,
        )
    ]
    return {"knowledge": knowledge, "cases": cases}


# ── CLI ───────────────────────────────────────────────────────────────────────

def _format_hit(i: int, hit: dict) -> str:
    heading = " > ".join(hit[h] for h in ("H1", "H2", "H3") if h in hit)
    score_parts = []
    if "rerank_score" in hit:
        score_parts.append(f"rerank={hit['rerank_score']:.4f}")
    if "structured_score" in hit:
        score_parts.append(f"structured={hit['structured_score']:.4f}")
    if "hierarchical_score" in hit:
        score_parts.append(f"hier={hit['hierarchical_score']:.4f}")
    if "rrf_score" in hit:
        score_parts.append(f"rrf={hit['rrf_score']:.4f}")
    if "distance" in hit:
        score_parts.append(f"dist={hit['distance']:.4f}")
    score_str = "  ".join(score_parts)
    lines = [
        f"[{i+1}] {hit['source']}  ({hit.get('doc_type', '?')})  {score_str}",
        f"    {heading}" if heading else "",
        f"    {hit['content'][:200]}{'…' if len(hit['content']) > 200 else ''}",
    ]
    return "\n".join(l for l in lines if l)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python pipeline/retrieve.py <query> [top_k] [doc_type] [mode]")
        sys.exit(1)

    query    = sys.argv[1]
    top_k    = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_TOP_K
    doc_type = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
    mode     = sys.argv[4] if len(sys.argv) > 4 else "hybrid+rerank"

    print(f"\n查询：{query}  (top_k={top_k}  mode={mode}"
          + (f"  doc_type={doc_type}" if doc_type else "") + ")\n")
    if mode == "with-cases":
        bundle = retrieve_with_cases(
            query, knowledge_top_k=top_k, case_top_k=3, doc_type=doc_type,
        )
        print("[标准、规程与论文证据]")
        for i, hit in enumerate(bundle["knowledge"]):
            print(_format_hit(i, hit))
            print()
        print("[相似案例]")
        for i, hit in enumerate(bundle["cases"]):
            print(_format_hit(i, hit))
            print()
        return

    hits = retrieve(query, top_k=top_k, doc_type=doc_type, mode=mode)
    for i, hit in enumerate(hits):
        print(_format_hit(i, hit))
        print()


if __name__ == "__main__":
    main()
