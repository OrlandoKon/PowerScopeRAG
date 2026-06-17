"""
pipeline/retrieve.py — Hybrid retrieval interface for PowerScopeRAG.

Supports three retrieval modes:
  dense          — vector-only (ChromaDB cosine similarity), original behaviour
  hybrid         — dense + BM25 fused via Reciprocal Rank Fusion
  hybrid+rerank  — hybrid + bge-reranker-v2-m3 cross-encoder re-ranking (default)

BGE query instruction prefix is applied to queries at retrieval time.

Usage:
    from pipeline.retrieve import retrieve
    results = retrieve("供电电压偏差限值")                 # hybrid+rerank (default)
    results = retrieve("台区低电压原因", mode="dense")     # vector-only
    results = retrieve("配变跳闸", top_k=3, mode="hybrid") # hybrid, no rerank

    # CLI demo
    python pipeline/retrieve.py "供电电压偏差限值"
    python pipeline/retrieve.py "供电电压偏差限值" 5 "" dense
"""

import json
import sys

import chromadb
import jieba
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from rank_bm25 import BM25Okapi

from config import (
    CHUNKS_FILE, COLLECTION_NAME, DEFAULT_TOP_K, EMBED_MODEL,
    QUERY_PREFIX, RERANK_MODEL, RRF_K, VECTORSTORE_DIR,
)

jieba.setLogLevel(20)  # suppress jieba initialisation noise

# ── Dense retrieval (ChromaDB) ────────────────────────────────────────────────

_ef = None
_collection = None


def _get_collection() -> chromadb.Collection:
    global _ef, _collection
    if _collection is not None:
        return _collection
    _ef = SentenceTransformerEmbeddingFunction(
        model_name=EMBED_MODEL,
        device="cpu",
        normalize_embeddings=True,
    )
    client = chromadb.PersistentClient(path=str(VECTORSTORE_DIR))
    _collection = client.get_collection(name=COLLECTION_NAME, embedding_function=_ef)
    return _collection


def _dense_search(query: str, top_k: int, doc_type: str | None) -> list[dict]:
    collection = _get_collection()
    where = {"doc_type": doc_type} if doc_type else None
    results = collection.query(
        query_texts=[QUERY_PREFIX + query],
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


# ── BM25 sparse retrieval ─────────────────────────────────────────────────────

_bm25: BM25Okapi | None = None
_bm25_chunks: list[dict] = []


def _get_bm25() -> tuple[BM25Okapi, list[dict]]:
    global _bm25, _bm25_chunks
    if _bm25 is not None:
        return _bm25, _bm25_chunks
    chunks: list[dict] = []
    with open(CHUNKS_FILE, encoding="utf-8") as f:
        for line in f:
            chunks.append(json.loads(line))
    tokenized = [list(jieba.cut(c["content"])) for c in chunks]
    _bm25 = BM25Okapi(tokenized)
    _bm25_chunks = chunks
    return _bm25, _bm25_chunks


def _bm25_search(query: str, top_k: int, doc_type: str | None) -> list[dict]:
    bm25, chunks = _get_bm25()
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
        pool.setdefault(c, h)

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
        _reranker = CrossEncoder(RERANK_MODEL, max_length=512, device="cpu")
    return _reranker


def rerank(query: str, chunks: list[dict], top_n: int) -> list[dict]:
    """Re-rank *chunks* for *query* using a cross-encoder. Returns top_n results."""
    if not chunks:
        return []
    reranker = _get_reranker()
    scores = reranker.predict([(query, c["content"]) for c in chunks])
    ranked = sorted(zip(scores, chunks), key=lambda x: x[0], reverse=True)
    return [{**c, "rerank_score": float(s)} for s, c in ranked[:top_n]]


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


# ── Public interface ──────────────────────────────────────────────────────────

def retrieve(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    doc_type: str | None = None,
    mode: str = "hybrid+rerank",
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
        mode:           "dense" | "hybrid" | "hybrid+rerank" (default)
        max_per_source: Max chunks per source doc in final results (default 2).
    """
    if mode == "dense":
        return _dense_search(query, top_k, doc_type)

    fetch_k = max(top_k * 3, 20)
    dense_hits  = _dense_search(query, fetch_k, doc_type)
    sparse_hits = _bm25_search(query, fetch_k, doc_type)
    fused = _rrf_fuse(dense_hits, sparse_hits)

    if mode == "hybrid":
        return _dedupe_by_source(fused, max_per_source)[:top_k]

    # hybrid+rerank: rerank a larger pool, then apply source diversity
    reranked = rerank(query, fused[:fetch_k], top_n=top_k * 3)
    return _dedupe_by_source(reranked, max_per_source)[:top_k]


# ── CLI ───────────────────────────────────────────────────────────────────────

def _format_hit(i: int, hit: dict) -> str:
    heading = " > ".join(hit[h] for h in ("H1", "H2", "H3") if h in hit)
    score_parts = []
    if "rerank_score" in hit:
        score_parts.append(f"rerank={hit['rerank_score']:.4f}")
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
    hits = retrieve(query, top_k=top_k, doc_type=doc_type, mode=mode)
    for i, hit in enumerate(hits):
        print(_format_hit(i, hit))
        print()


if __name__ == "__main__":
    main()
