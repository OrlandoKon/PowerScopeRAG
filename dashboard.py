"""Build live summary data for the public knowledge-base dashboard."""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from config import (
    ACTIVE_COLLECTION_NAME,
    CASES_FILE,
    CHUNKS_FILE,
    DEFAULT_RETRIEVAL_MODE,
    EMBED_MODEL,
    RERANK_MODEL,
    ROOT,
)


TYPE_META = {
    "regulation": ("标准规范", "regulation"),
    "journal": ("期刊论文", "journal"),
    "thesis": ("学位论文", "thesis"),
}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _latest_evaluation() -> dict:
    reports = sorted(
        (ROOT / "tests" / "reports").glob("agentic_200_comparison_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        ragas = report.get("ragas", {}).get("agentic_refined")
        retrieval = report.get("retrieval", {}).get("agentic_refined")
        if ragas and retrieval and ragas.get("completed") == 200:
            return {
                "label": "Agentic RAG",
                "questions": ragas["completed"],
                "updated_at": datetime.fromtimestamp(
                    path.stat().st_mtime
                ).astimezone().isoformat(timespec="minutes"),
                "retrieval": {
                    "hit_at_1": retrieval["hit_at_1"],
                    "hit_at_3": retrieval["hit_at_3"],
                    "hit_at_5": retrieval["hit_at_5"],
                    "mrr": retrieval["mrr"],
                },
                "ragas": {
                    "faithfulness": ragas["faithfulness"],
                    "answer_relevancy": ragas["answer_relevancy"],
                    "context_precision": ragas["context_precision"],
                    "context_recall": ragas["context_recall"],
                    "answer_correctness": ragas["answer_correctness"],
                },
            }
    return {}


def build_dashboard_data(collection_count: int | None) -> dict:
    chunks = _read_jsonl(CHUNKS_FILE)
    cases = _read_jsonl(CASES_FILE)

    sources: dict[str, dict] = {}
    category_chunks: dict[str, int] = defaultdict(int)
    for chunk in chunks:
        source = str(chunk.get("source") or "未知来源")
        doc_type = str(chunk.get("doc_type") or "unknown")
        category_chunks[doc_type] += 1
        row = sources.setdefault(
            source,
            {"name": source.removesuffix(".md"), "source": source,
             "type": doc_type, "chunks": 0},
        )
        row["chunks"] += 1

    documents = sorted(
        sources.values(),
        key=lambda item: (
            list(TYPE_META).index(item["type"])
            if item["type"] in TYPE_META else len(TYPE_META),
            item["name"],
        ),
    )
    categories = []
    for doc_type, (label, key) in TYPE_META.items():
        category_documents = [item for item in documents if item["type"] == doc_type]
        categories.append({
            "key": key,
            "label": label,
            "documents": len(category_documents),
            "chunks": category_chunks[doc_type],
        })

    data_files = [path for path in (CHUNKS_FILE, CASES_FILE) if path.exists()]
    indexed_at = max((path.stat().st_mtime for path in data_files), default=0)
    return {
        "status": "ready" if collection_count is not None else "loading",
        "overview": {
            "documents": len(documents),
            "chunks": len(chunks),
            "vector_records": collection_count or 0,
            "cases": len(cases),
            "case_sources": len({case.get("source") for case in cases}),
        },
        "categories": categories,
        "documents": documents,
        "cases": {
            "sources": sorted({str(case.get("source")) for case in cases}),
        },
        "system": {
            "collection": ACTIVE_COLLECTION_NAME,
            "retrieval_mode": DEFAULT_RETRIEVAL_MODE,
            "embedding_model": EMBED_MODEL,
            "reranker_model": RERANK_MODEL,
            "vector_database": "ChromaDB / HNSW cosine",
            "indexed_at": (
                datetime.fromtimestamp(indexed_at).astimezone().isoformat(timespec="minutes")
                if indexed_at else None
            ),
        },
        "evaluation": _latest_evaluation(),
    }
