"""
pipeline/retrieve.py — Hybrid retrieval interface for PowerScopeRAG.

Dense retrieval via Chroma (BAAI/bge-large-zh-v1.5 cosine similarity).
BGE query instruction prefix is applied to queries at retrieval time.

Usage:
    from pipeline.retrieve import retrieve
    results = retrieve("供电电压偏差限值", top_k=5)

    # Filter by document type
    results = retrieve("台区低电压原因", doc_type="journal", top_k=5)

    # CLI demo
    python pipeline/retrieve.py "供电电压偏差限值"
"""

import sys
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

ROOT = Path(__file__).parent.parent
VECTORSTORE_DIR = ROOT / "data" / "vectorstore"

MODEL_NAME = "BAAI/bge-large-zh-v1.5"
COLLECTION_NAME = "powerscoperag"

# BGE instruction prefix — prepend to queries only (not documents)
_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

_ef = None
_collection = None


def _get_collection() -> chromadb.Collection:
    global _ef, _collection
    if _collection is not None:
        return _collection

    _ef = SentenceTransformerEmbeddingFunction(
        model_name=MODEL_NAME,
        device="cpu",
        normalize_embeddings=True,
    )
    client = chromadb.PersistentClient(path=str(VECTORSTORE_DIR))
    _collection = client.get_collection(
        name=COLLECTION_NAME,
        embedding_function=_ef,
    )
    return _collection


def retrieve(
    query: str,
    top_k: int = 5,
    doc_type: str | None = None,
) -> list[dict]:
    """
    Return top_k chunks most relevant to *query*.

    Each result dict contains:
        content, source, doc_type, H1, H2 (optional), H3 (optional),
        chunk_idx, distance (cosine distance, lower = more similar)

    Args:
        query:    Natural language question in Chinese or English.
        top_k:   Number of results to return.
        doc_type: Optional filter — "regulation", "journal", or "thesis".
    """
    collection = _get_collection()

    where = {"doc_type": doc_type} if doc_type else None

    results = collection.query(
        query_texts=[_QUERY_PREFIX + query],
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
        hits.append({"content": doc, "distance": dist, **meta})

    return hits


def _format_hit(i: int, hit: dict) -> str:
    heading = " > ".join(
        hit[h] for h in ("H1", "H2", "H3") if h in hit
    )
    lines = [
        f"[{i+1}] {hit['source']}  ({hit['doc_type']})  dist={hit['distance']:.4f}",
        f"    {heading}" if heading else "",
        f"    {hit['content'][:200]}{'…' if len(hit['content']) > 200 else ''}",
    ]
    return "\n".join(l for l in lines if l)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python pipeline/retrieve.py <query> [top_k] [doc_type]")
        sys.exit(1)

    query = sys.argv[1]
    top_k = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    doc_type = sys.argv[3] if len(sys.argv) > 3 else None

    hits = retrieve(query, top_k=top_k, doc_type=doc_type)
    print(f"\n查询：{query}  (top {top_k}{'  doc_type=' + doc_type if doc_type else ''})\n")
    for i, hit in enumerate(hits):
        print(_format_hit(i, hit))
        print()


if __name__ == "__main__":
    main()
