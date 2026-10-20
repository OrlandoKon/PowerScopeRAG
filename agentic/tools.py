"""Whitelisted retrieval tools exposed to the Agentic RAG orchestrator."""

from decimal import Decimal

from pipeline.retrieve import retrieve, retrieve_cases, retrieve_document_sections

from agentic.schemas import CalculationSpec, RetrievalTask


def _rank_value(hit: dict) -> float:
    if "hierarchical_score" in hit:
        return float(hit["hierarchical_score"])
    if "rerank_score" in hit:
        return float(hit["rerank_score"])
    return -float(hit.get("distance", 1.0))


def _dedupe_hits(hits: list[dict], top_k: int) -> list[dict]:
    unique: dict[tuple, dict] = {}
    for hit in hits:
        key = (hit.get("source"), hit.get("chunk_idx", hit.get("case_id")))
        current = unique.get(key)
        if current is None or _rank_value(hit) > _rank_value(current):
            unique[key] = hit
    return sorted(unique.values(), key=_rank_value, reverse=True)[:top_k]


def _source_diverse_hits(hits: list[dict], top_k: int) -> list[dict]:
    ranked = _dedupe_hits(hits, len(hits))
    selected = []
    seen_sources = set()
    for hit in ranked:
        source = hit.get("source")
        if source in seen_sources:
            continue
        selected.append(hit)
        seen_sources.add(source)
        if len(selected) == top_k:
            return selected
    selected_keys = {
        (hit.get("source"), hit.get("chunk_idx", hit.get("case_id")))
        for hit in selected
    }
    for hit in ranked:
        key = (hit.get("source"), hit.get("chunk_idx", hit.get("case_id")))
        if key in selected_keys:
            continue
        selected.append(hit)
        if len(selected) == top_k:
            break
    return selected


def _merge_professional_hits(
    journal_hits: list[dict],
    thesis_hits: list[dict],
    top_k: int,
) -> list[dict]:
    if top_k <= 1:
        return _dedupe_hits(journal_hits + thesis_hits, top_k)
    thesis_quota = max(1, top_k // 4)
    journal_quota = top_k - thesis_quota
    selected = _source_diverse_hits(journal_hits, journal_quota)
    selected += _source_diverse_hits(thesis_hits, thesis_quota)
    if len(selected) < top_k:
        selected += _dedupe_hits(journal_hits + thesis_hits, top_k)
    return _dedupe_hits(selected, top_k)


def _format_number(value: Decimal) -> str:
    rendered = format(value.quantize(Decimal("0.01")), "f")
    return rendered.rstrip("0").rstrip(".") or "0"


def execute_numeric_calculation(spec: CalculationSpec) -> dict:
    left = Decimal(str(spec.left_value))
    right = Decimal(str(spec.right_value))
    unit = spec.unit

    if spec.operation == "ratio":
        if right == 0:
            raise ValueError("ratio denominator cannot be zero")
        percentage = left / right * Decimal("100")
        content = (
            f"{spec.left_label} / {spec.right_label} = "
            f"{_format_number(left)}{unit} / {_format_number(right)}{unit} "
            f"= {_format_number(percentage)}%。"
        )
        result = {"ratio_percent": float(percentage)}
        excess = percentage - Decimal("100")
        if excess > 0:
            content = content[:-1] + f"，相当于超出{_format_number(excess)}%。"
            result["excess_percent"] = float(excess)
    else:
        difference = left - right
        relation = "大于" if difference > 0 else "小于" if difference < 0 else "等于"
        content = (
            f"{spec.left_label}{_format_number(left)}{unit}{relation}"
            f"{spec.right_label}{_format_number(right)}{unit}，"
            f"差值为{_format_number(abs(difference))}{unit}"
        )
        result = {
            "relation": relation,
            "difference": float(difference),
        }
        if right != 0:
            percent = difference / abs(right) * Decimal("100")
            content += f"，相对{spec.right_label}{'超出' if percent > 0 else '低于'}{_format_number(abs(percent))}%"
            result["relative_percent"] = float(percent)
        content += "。"

    return {
        "content": content,
        "source": "确定性数值计算",
        "doc_type": "calculation",
        "chunk_idx": f"calculation:{spec.operation}:{spec.left_value}:{spec.right_value}:{unit}",
        "evidence_type": "calculation",
        "calculation": {**spec.model_dump(), **result},
    }


def execute_retrieval_task(
    task: RetrievalTask,
    knowledge_top_k: int = 4,
    case_top_k: int = 3,
) -> list[dict]:
    if task.tool == "calculate":
        return [execute_numeric_calculation(task.calculation)]

    if task.tool == "search_document":
        hits = retrieve_document_sections(
            task.query,
            source=task.source,
            top_k=knowledge_top_k,
        )
        results = []
        for hit in hits:
            row = {**hit, "evidence_type": "knowledge"}
            if hit.get("body_content"):
                row["content"] = hit["body_content"]
                row.pop("context_format", None)
            results.append(row)
        return results

    if task.tool == "search_cases" or task.lane == "cases":
        return [
            {**hit, "evidence_type": "case"}
            for hit in retrieve_cases(task.query, top_k=case_top_k)
        ]

    if task.lane == "regulation":
        hits = retrieve(task.query, top_k=knowledge_top_k, doc_type="regulation")
    elif task.lane == "professional":
        journal_hits = retrieve(
            task.query, top_k=knowledge_top_k, doc_type="journal",
        )
        thesis_hits = retrieve(
            task.query, top_k=knowledge_top_k, doc_type="thesis",
        )
        hits = _merge_professional_hits(
            journal_hits, thesis_hits, knowledge_top_k,
        )
    else:
        hits = retrieve(task.query, top_k=knowledge_top_k)
    return [{**hit, "evidence_type": "knowledge"} for hit in hits]
