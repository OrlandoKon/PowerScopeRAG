"""Assemble answer context from executed retrieval tasks."""

import json
import re
from functools import lru_cache

import jieba.analyse

from config import CHUNKS_FILE


@lru_cache(maxsize=1)
def _original_chunks() -> dict[tuple[str, int], dict]:
    if not CHUNKS_FILE.exists():
        return {}
    rows = (json.loads(line) for line in CHUNKS_FILE.read_text(encoding="utf-8").splitlines() if line)
    return {
        (str(row["source"]), row["chunk_idx"]): row
        for row in rows
        if isinstance(row.get("chunk_idx"), int)
    }


def _chunk_indices(hit: dict) -> list[int]:
    index = hit.get("chunk_idx")
    if isinstance(index, int):
        return [index]
    match = re.fullmatch(r"window:(\d+)-(\d+)", str(index))
    return list(range(int(match[1]), int(match[2]) + 1)) if match else []


def _restore_original(hit: dict, chunks: dict[tuple[str, int], dict]) -> dict:
    source = str(hit.get("source", ""))
    originals = [chunks[(source, index)] for index in _chunk_indices(hit)
                 if (source, index) in chunks]
    if not originals:
        return hit
    restored = {
        **hit,
        "content": "\n\n".join(row["content"].strip() for row in originals),
    }
    restored.pop("context_format", None)
    return restored


def refine_answer_context(
    query: str, hits: list[dict], *, allow_search: bool = True,
) -> list[dict]:
    """Restore selected raw evidence while keeping the remaining context compact."""
    if not hits:
        return []
    chunks = _original_chunks()
    if not chunks:
        return hits

    selected = list(hits)
    if not allow_search:
        selected[0] = _restore_original(selected[0], chunks)
        best_extra = None
        best_gain = 0.03
        for index, hit in enumerate(selected[1:], 1):
            restored = _restore_original(hit, chunks)
            extra_length = len(restored["content"]) - len(hit["content"])
            if extra_length <= 0 or extra_length > 400:
                continue
            gain = _lexical_coverage(query, restored) - _lexical_coverage(query, hit)
            if gain > best_gain:
                best_extra = (index, restored)
                best_gain = gain
        if best_extra:
            selected[best_extra[0]] = best_extra[1]
        return selected

    source = str(selected[0].get("source", ""))
    if source and any((source, index) in chunks for index in _chunk_indices(selected[0])):
        from pipeline.retrieve import _source_sparse_candidates

        candidates = [
            item for item in _source_sparse_candidates(query, [source], per_source=12)
            if isinstance(item.get("chunk_idx"), int)
        ]
        if candidates:
            best = max(candidates, key=lambda item: item["bm25_score"])
            represented = {
                index for item in selected if item.get("source") == source
                for index in _chunk_indices(item)
            }
            candidate_scores = {
                item["chunk_idx"]: item["bm25_score"] for item in candidates
            }
            strongest_present = max(
                (candidate_scores.get(index, 0.0) for index in represented),
                default=0.0,
            )
            if (
                best["chunk_idx"] not in represented
                and best["bm25_score"] > strongest_present * 1.03
            ):
                replace_at = next(
                    (index for index in range(len(selected) - 1, 0, -1)
                     if selected[index].get("source") == source),
                    len(selected) - 1,
                )
                selected[replace_at] = best

            # Structured excerpts are concise but can omit a number or list item.
            # Restore only the strongest raw chunk represented by the lead hit;
            # keeping the other four excerpts compact avoids context dilution.
            lead_indices = set(_chunk_indices(selected[0]))
            lead_candidates = [
                item for item in candidates if item["chunk_idx"] in lead_indices
            ]
            if lead_candidates:
                selected[0] = max(
                    lead_candidates, key=lambda item: item["bm25_score"]
                )
            else:
                selected[0] = _restore_original(selected[0], chunks)

    return selected

def _identity(hit: dict) -> tuple[str, str]:
    return str(hit.get("source", "")), str(hit.get("chunk_idx", ""))


def _lexical_coverage(query: str, hit: dict) -> float:
    terms = jieba.analyse.extract_tags(query, topK=16, withWeight=True)
    if not terms:
        return 0.0
    text = "\n".join(str(hit.get(key) or "") for key in (
        "source", "section", "H1", "H2", "H3", "content",
    )).lower()
    denominator = sum(weight for _, weight in terms)
    return sum(
        weight for term, weight in terms if term.lower() in text
    ) / denominator


def _source_name_coverage(query: str, source: str) -> float:
    name = re.sub(r"\.(?:md|pdf|docx|txt)$", "", source, flags=re.I)
    terms = jieba.analyse.extract_tags(
        name.replace("_", " "), topK=16, withWeight=True,
    )
    if not terms:
        return 0.0
    denominator = sum(weight for _, weight in terms)
    return sum(
        weight for term, weight in terms if term.lower() in query.lower()
    ) / denominator


def _explicit_author_mentioned(query: str, source: str) -> bool:
    stem = re.sub(r"\.(?:md|pdf|docx|txt)$", "", source, flags=re.I)
    author = stem.rsplit("_", 1)[-1]
    return bool(re.fullmatch(r"[\u4e00-\u9fff]{2,4}", author) and author in query)


def _section_coverage(query: str, section: str) -> float:
    terms = jieba.analyse.extract_tags(query, topK=12, withWeight=True)
    if not terms:
        return 0.0
    section = str(section or "").lower()
    return sum(
        weight for term, weight in terms if term.lower() in section
    ) / sum(weight for _, weight in terms)


def select_agentic_evidence(result: dict, top_k: int = 5) -> list[dict]:
    """Select diverse evidence from the Agentic execution trace."""
    evidence_by_id = {
        item["evidence_id"]: item for item in result.get("evidence", [])
    }
    groups = [
        [evidence_by_id[item_id] for item_id in step.get("evidence_ids", [])
         if item_id in evidence_by_id]
        for step in result.get("trace", [])
    ]
    selected = []
    seen = set()
    for rank in range(max(map(len, groups), default=0)):
        for group in groups:
            if rank >= len(group):
                continue
            item = group[rank]
            key = _identity(item)
            if key in seen:
                continue
            selected.append(item)
            seen.add(key)
            if len(selected) == top_k:
                return selected
    return selected


def assemble_answer_context(
    query: str,
    ordinary_hits: list[dict],
    agentic_hits: list[dict],
    top_k: int = 5,
    *,
    allow_cross_source: bool = False,
) -> list[dict]:
    """Preserve reliable ordinary hits while admitting complementary evidence."""
    if top_k <= 0:
        return []
    candidates = []
    seen = set()
    trusted_sources = {
        hit.get("source") for hit in ordinary_hits[:2]
    }
    needs_cross_source = (
        "分别查找" in query
        or ("案例" in query and any(
            term in query for term in ("标准", "规程", "规范", "机理")
        ))
    )
    for index, hit in enumerate(ordinary_hits):
        key = _identity(hit)
        if key in seen:
            continue
        seen.add(key)
        candidates.append((hit, index, False))
    for index, hit in enumerate(agentic_hits):
        if (ordinary_hits and not (needs_cross_source or allow_cross_source)
                and hit.get("source") not in trusted_sources):
            continue
        key = _identity(hit)
        if key in seen:
            continue
        seen.add(key)
        candidates.append((hit, index, True))

    def score(candidate: tuple[dict, int, bool]) -> float:
        hit, rank, from_agentic = candidate
        prior = (0.18 / (rank + 1)) if not from_agentic else 0.0
        return _lexical_coverage(query, hit) + prior

    ranked = sorted(candidates, key=score, reverse=True)
    anchor_ids = {
        _identity(hit) for hit in ordinary_hits[:min(2, top_k)]
    }
    anchors = [
        item for item in candidates if _identity(item[0]) in anchor_ids
    ]
    selected = anchors + [
        item for item in ranked if _identity(item[0]) not in anchor_ids
    ][:top_k - len(anchors)]
    selected.sort(key=score, reverse=True)
    if ordinary_hits and selected:
        anchor_key = _identity(ordinary_hits[0])
        anchor = next(
            (item for item in selected if _identity(item[0]) == anchor_key),
            None,
        )
        if anchor and score(selected[0]) - score(anchor) <= 0.25:
            selected.remove(anchor)
            selected.insert(0, anchor)
    return [hit for hit, _, _ in selected]


def assemble_planned_answer_context(
    query: str, result: dict, top_k: int = 5,
) -> list[dict]:
    """Use the first planned knowledge task as the ranked anchor."""
    evidence_by_id = {
        item["evidence_id"]: item for item in result.get("evidence", [])
    }
    steps = result.get("trace", [])
    primary_step = next(
        (step for step in steps if step["task"]["tool"] in
         {"search_knowledge", "search_document"}),
        None,
    )
    primary = [
        evidence_by_id[item_id] for item_id in primary_step["evidence_ids"]
        if item_id in evidence_by_id
    ] if primary_step else []
    selected = assemble_answer_context(
        query,
        primary,
        select_agentic_evidence(result, top_k=len(evidence_by_id)),
        top_k,
        allow_cross_source=len(steps) > 1,
    )
    if selected and len(selected) >= top_k:
        trusted_sources = {hit.get("source") for hit in selected[:2]}
        selected_ids = {_identity(hit) for hit in selected}
        for step in steps:
            if step["task"]["tool"] != "search_document":
                continue
            scoped = next(
                (evidence_by_id[item_id] for item_id in step["evidence_ids"]
                 if item_id in evidence_by_id),
                None,
            )
            if (scoped is None or scoped.get("source") not in trusted_sources
                    or _identity(scoped) in selected_ids
                    or _section_coverage(query, scoped.get("section", "")) < 1 / 3):
                continue
            selected.pop()
            selected.insert(0, _restore_original(scoped, _original_chunks()))
            break
    if selected:
        lead_source = selected[0].get("source", "")
        lead_match = _source_name_coverage(query, lead_source)
        scoped_sources = {
            step["task"].get("source")
            for step in steps if step["task"]["tool"] == "search_document"
            and step.get("evidence_ids")
        }
        for source in sorted(
            scoped_sources,
            key=lambda item: _source_name_coverage(query, item),
            reverse=True,
        ):
            if source == lead_source:
                continue
            match = _source_name_coverage(query, source)
            if (not _explicit_author_mentioned(query, source)
                    and (match < 0.55 or match - lead_match < 0.25)):
                continue
            candidate = next(
                (hit for hit in selected if hit.get("source") == source), None,
            )
            if candidate is not None:
                selected.remove(candidate)
                selected.insert(0, candidate)
                break
    if selected:
        named_sources = {
            hit.get("source") for hit in selected
            if _explicit_author_mentioned(query, hit.get("source", ""))
        }
        trusted_sources = named_sources or {
            hit.get("source") for hit in selected[:2]
        }
        selected_by_key = {_identity(hit): hit for hit in selected}
        promoted = []
        promoted_keys = set()
        for item_id in result.get("answer_evidence_ids", [])[:5]:
            candidate = evidence_by_id.get(item_id)
            if candidate is None or candidate.get("source") not in trusted_sources:
                continue
            key = _identity(candidate)
            if key in promoted_keys:
                continue
            promoted.append(selected_by_key.get(key, candidate))
            promoted_keys.add(key)
        if promoted:
            selected = (
                promoted + [hit for hit in selected
                            if _identity(hit) not in promoted_keys]
            )[:top_k]
    answer_hits = []
    for hit in selected:
        if hit.get("evidence_type") != "case":
            answer_hits.append(hit)
            continue
        status = hit.get("verification_status") or "未标注"
        case_id = hit.get("case_id") or "未标注"
        answer_hits.append({
            **hit,
            "content": (
                f"历史案例 {case_id}（核验状态：{status}；仅作类比，"
                f"不能直接认定为当前事件事实）：\n{hit['content']}"
            ),
        })
    return answer_hits
