"""
api.py — FastAPI retrieval + generation service for PowerScopeRAG.

Start:
    uvicorn api:app --host "::" --port 8000

Endpoints:
    GET  /          health check
    GET  /stats     vector store statistics
    POST /retrieve  semantic search (retrieve only)
    POST /ask       RAG full pipeline (retrieve + DeepSeek generation)
"""

from contextlib import asynccontextmanager
from typing import Literal

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from config import VECTORSTORE_DIR, EMBED_MODEL, COLLECTION_NAME, QUERY_PREFIX, DEFAULT_TOP_K
from pipeline.generate import rag_answer

# ── Shared state ──────────────────────────────────────────────────────────────

_collection: chromadb.Collection | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _collection
    print(f"Loading embedding model: {EMBED_MODEL} ...")
    ef = SentenceTransformerEmbeddingFunction(
        model_name=EMBED_MODEL,
        device="cpu",
        normalize_embeddings=True,
    )
    client = chromadb.PersistentClient(path=str(VECTORSTORE_DIR))
    _collection = client.get_collection(name=COLLECTION_NAME, embedding_function=ef)
    print(f"Vector store ready: {_collection.count()} documents")
    yield
    _collection = None


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="PowerScopeRAG Retrieval API",
    description="400V低压配电网停电原因分析知识库检索服务",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Schemas ───────────────────────────────────────────────────────────────────

class RetrieveRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="检索问题")
    top_k: int = Field(DEFAULT_TOP_K, ge=1, le=20, description="返回结果数量")
    doc_type: Literal["regulation", "journal", "thesis"] | None = Field(
        None, description="文档类型过滤（不填则检索全部）"
    )


class ChunkResult(BaseModel):
    content: str
    source: str
    doc_type: str
    H1: str | None = None
    H2: str | None = None
    H3: str | None = None
    chunk_idx: int
    distance: float = Field(description="余弦距离，越小越相关")


class RetrieveResponse(BaseModel):
    query: str
    results: list[ChunkResult]


class StatsResponse(BaseModel):
    total_documents: int
    collection: str
    model: str
    vectorstore_path: str


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="用户问题")
    top_k: int = Field(DEFAULT_TOP_K, ge=1, le=20, description="检索片段数量")
    doc_type: Literal["regulation", "journal", "thesis"] | None = Field(
        None, description="文档类型过滤（不填则检索全部）"
    )


class AskResponse(BaseModel):
    query: str
    answer: str
    sources: list[ChunkResult]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/", summary="健康检查")
def health():
    return {"status": "ok", "service": "PowerScopeRAG Retrieval API"}


@app.get("/stats", response_model=StatsResponse, summary="向量库统计")
def stats():
    if _collection is None:
        raise HTTPException(status_code=503, detail="Vector store not ready")
    return StatsResponse(
        total_documents=_collection.count(),
        collection=COLLECTION_NAME,
        model=EMBED_MODEL,
        vectorstore_path=str(VECTORSTORE_DIR),
    )


@app.post("/retrieve", response_model=RetrieveResponse, summary="语义检索")
def retrieve(req: RetrieveRequest):
    if _collection is None:
        raise HTTPException(status_code=503, detail="Vector store not ready")

    where = {"doc_type": req.doc_type} if req.doc_type else None

    results = _collection.query(
        query_texts=[QUERY_PREFIX + req.query],
        n_results=req.top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    hits = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        hits.append(ChunkResult(
            content=doc,
            distance=dist,
            **{k: meta.get(k) for k in ("source", "doc_type", "H1", "H2", "H3", "chunk_idx")},
        ))

    return RetrieveResponse(query=req.query, results=hits)


@app.post("/ask", response_model=AskResponse, summary="RAG 问答（检索 + 生成）")
def ask(req: AskRequest):
    if _collection is None:
        raise HTTPException(status_code=503, detail="Vector store not ready")

    where = {"doc_type": req.doc_type} if req.doc_type else None

    results = _collection.query(
        query_texts=[QUERY_PREFIX + req.query],
        n_results=req.top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    hits = []
    chunks_for_llm = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        hits.append(ChunkResult(
            content=doc,
            distance=dist,
            **{k: meta.get(k) for k in ("source", "doc_type", "H1", "H2", "H3", "chunk_idx")},
        ))
        chunks_for_llm.append({"content": doc, **meta})

    try:
        answer = rag_answer(req.query, chunks_for_llm)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return AskResponse(query=req.query, answer=answer, sources=hits)
