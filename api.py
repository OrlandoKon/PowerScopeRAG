"""
api.py — FastAPI retrieval + generation service for PowerScopeRAG.

Start:
    uvicorn api:app --host "::" --port 8000

Public endpoints:
    GET  /          knowledge-base dashboard
    GET  /health    health check
    POST /rag       Agentic evidence retrieval, optionally with an answer
"""

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from agentic.assembly import (
    assemble_planned_answer_context,
    refine_answer_context,
)
from agentic.orchestrator import run_agentic_retrieval
from agentic.schemas import AgenticRetrievalResult
from dashboard import build_dashboard_data
from config import (
    ACTIVE_COLLECTION_NAME,
    DEFAULT_RETRIEVAL_MODE,
    DEFAULT_TOP_K,
    EMBED_MODEL,
    ROOT,
    VECTORSTORE_DIR,
)
from pipeline.generate import rag_answer, rag_diagnose, review_answer
from pipeline.retrieve import initialize_retrieval, retrieve as retrieve_chunks, retrieve_with_cases

# ── Shared state ──────────────────────────────────────────────────────────────

_main_collection_count: int | None = None
DASHBOARD_FILE = ROOT / "web" / "dashboard.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _main_collection_count
    print(f"Loading embedding model: {EMBED_MODEL} ...")
    _main_collection_count = initialize_retrieval(ACTIVE_COLLECTION_NAME)
    print(f"Vector store ready: {_main_collection_count} documents")
    yield
    _main_collection_count = None


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
    chunk_idx: int | str = Field(
        description="原始切片序号；层级检索合并窗口使用 window:start-end"
    )
    distance: float | None = Field(
        default=None, description="余弦距离；仅在检索结果提供该值时返回"
    )


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


class DiagnoseRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="停电现象与可用数据")
    knowledge_top_k: int = Field(DEFAULT_TOP_K, ge=1, le=20, description="规范知识数量")
    case_top_k: int = Field(3, ge=1, le=10, description="相似案例数量")
    doc_type: Literal["regulation", "journal", "thesis"] | None = Field(
        None, description="可选的主知识库类型过滤"
    )


class CaseResult(BaseModel):
    case_id: str
    cause_code: str
    cause_name: str
    trigger: str = ""
    observations: list[str] = Field(default_factory=list)
    outage_scope: str = ""
    final_cause: str
    action: str = ""
    case_text: str
    source: str
    verification_status: str
    rerank_score: float | None = None


class DiagnoseResponse(BaseModel):
    query: str
    answer: str
    knowledge_sources: list[ChunkResult]
    similar_cases: list[CaseResult]


class AgenticRetrieveRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="知识取证任务")
    max_follow_up_rounds: int = Field(1, ge=0, le=2, description="最大补充检索轮数")
    knowledge_top_k: int = Field(4, ge=1, le=10, description="每个知识任务返回片段数")
    case_top_k: int = Field(3, ge=1, le=10, description="每个案例任务返回案例数")
    include_trace: bool = Field(True, description="是否返回执行轨迹")


class AgenticAskRequest(AgenticRetrieveRequest):
    top_k: int = Field(5, ge=1, le=10, description="用于生成回答的融合证据数量")


class RagRequest(AgenticAskRequest):
    generate_answer: bool = Field(
        False, description="是否根据检索证据生成回答；默认只返回证据"
    )


class RagResponse(AgenticRetrievalResult):
    answer: str | None = None
    sources: list[ChunkResult] = Field(default_factory=list)


class AgenticAskResponse(AskResponse):
    sufficient: bool
    assessment: str
    missing_data: list[str]
    conflicts: list[str]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    return HTMLResponse(
        DASHBOARD_FILE.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/health", summary="健康检查")
def health():
    return {"status": "ok", "service": "PowerScopeRAG Retrieval API"}


@app.get("/dashboard-data", include_in_schema=False)
def dashboard_data():
    return build_dashboard_data(_main_collection_count)


@app.get("/stats", response_model=StatsResponse, include_in_schema=False)
def stats():
    if _main_collection_count is None:
        raise HTTPException(status_code=503, detail="Vector store not ready")
    return StatsResponse(
        total_documents=_main_collection_count,
        collection=ACTIVE_COLLECTION_NAME,
        model=EMBED_MODEL,
        vectorstore_path=str(VECTORSTORE_DIR),
    )


@app.post("/retrieve", response_model=RetrieveResponse, include_in_schema=False)
def retrieve(req: RetrieveRequest):
    if _main_collection_count is None:
        raise HTTPException(status_code=503, detail="Vector store not ready")

    raw_hits = retrieve_chunks(
        req.query,
        top_k=req.top_k,
        doc_type=req.doc_type,
        mode=DEFAULT_RETRIEVAL_MODE,
    )
    hits = [
        ChunkResult(
            content=hit["content"],
            source=hit["source"],
            doc_type=hit["doc_type"],
            H1=hit.get("H1"),
            H2=hit.get("H2"),
            H3=hit.get("H3"),
            chunk_idx=hit["chunk_idx"],
            distance=hit["distance"],
        )
        for hit in raw_hits
    ]

    return RetrieveResponse(query=req.query, results=hits)


@app.post("/ask", response_model=AskResponse, include_in_schema=False)
def ask(req: AskRequest):
    if _main_collection_count is None:
        raise HTTPException(status_code=503, detail="Vector store not ready")

    chunks_for_llm = retrieve_chunks(
        req.query,
        top_k=req.top_k,
        doc_type=req.doc_type,
        mode=DEFAULT_RETRIEVAL_MODE,
    )
    hits = [
        ChunkResult(
            content=hit["content"],
            source=hit["source"],
            doc_type=hit["doc_type"],
            H1=hit.get("H1"),
            H2=hit.get("H2"),
            H3=hit.get("H3"),
            chunk_idx=hit["chunk_idx"],
            distance=hit["distance"],
        )
        for hit in chunks_for_llm
    ]

    try:
        answer = rag_answer(req.query, chunks_for_llm)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return AskResponse(query=req.query, answer=answer, sources=hits)


@app.post("/diagnose", response_model=DiagnoseResponse, include_in_schema=False)
def diagnose(req: DiagnoseRequest):
    if _main_collection_count is None:
        raise HTTPException(status_code=503, detail="Vector store not ready")
    try:
        evidence = retrieve_with_cases(
            req.query,
            knowledge_top_k=req.knowledge_top_k,
            case_top_k=req.case_top_k,
            doc_type=req.doc_type,
        )
        answer = rag_diagnose(req.query, evidence["knowledge"], evidence["cases"])
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=503, detail=str(e))

    knowledge_sources = [
        ChunkResult(
            content=hit["content"],
            source=hit["source"],
            doc_type=hit["doc_type"],
            H1=hit.get("H1"),
            H2=hit.get("H2"),
            H3=hit.get("H3"),
            chunk_idx=hit["chunk_idx"],
            distance=hit.get("distance"),
        )
        for hit in evidence["knowledge"]
    ]
    similar_cases = [
        CaseResult(
            case_id=hit["case_id"],
            cause_code=hit["cause_code"],
            cause_name=hit["cause_name"],
            trigger=hit.get("trigger", ""),
            observations=hit.get("observations", []),
            outage_scope=hit.get("outage_scope", ""),
            final_cause=hit["final_cause"],
            action=hit.get("action", ""),
            case_text=hit["case_text"],
            source=hit["source"],
            verification_status=hit["verification_status"],
            rerank_score=hit.get("rerank_score"),
        )
        for hit in evidence["cases"]
    ]
    return DiagnoseResponse(
        query=req.query,
        answer=answer,
        knowledge_sources=knowledge_sources,
        similar_cases=similar_cases,
    )


@app.post(
    "/agentic-retrieve",
    response_model=AgenticRetrievalResult,
    include_in_schema=False,
)
def agentic_retrieve(req: AgenticRetrieveRequest):
    if _main_collection_count is None:
        raise HTTPException(status_code=503, detail="Vector store not ready")
    try:
        return run_agentic_retrieval(
            req.query,
            max_follow_up_rounds=req.max_follow_up_rounds,
            knowledge_top_k=req.knowledge_top_k,
            case_top_k=req.case_top_k,
            include_trace=req.include_trace,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _generate_agentic_answer(
    query: str,
    result: AgenticRetrievalResult,
    top_k: int,
) -> tuple[str, list[ChunkResult]]:
    answer_hits = assemble_planned_answer_context(query, result.model_dump(), top_k)
    answer_hits = refine_answer_context(query, answer_hits, allow_search=False)
    answer = rag_answer(query, answer_hits)
    answer = review_answer(query, answer_hits, answer)
    sources = [
        ChunkResult(
            content=hit["content"],
            source=hit["source"],
            doc_type=hit.get("doc_type") or hit.get("evidence_type", ""),
            H1=hit.get("section") or hit.get("H1"),
            H2=hit.get("H2"),
            H3=hit.get("H3"),
            chunk_idx=hit.get("chunk_idx") if hit.get("chunk_idx") is not None else "",
            distance=hit.get("distance"),
        )
        for hit in answer_hits
    ]
    return answer, sources


@app.post("/rag", response_model=RagResponse, summary="Agentic RAG")
def rag(req: RagRequest):
    if _main_collection_count is None:
        raise HTTPException(status_code=503, detail="Vector store not ready")
    try:
        result = run_agentic_retrieval(
            req.query,
            max_follow_up_rounds=req.max_follow_up_rounds,
            knowledge_top_k=req.knowledge_top_k,
            case_top_k=req.case_top_k,
            include_trace=req.include_trace or req.generate_answer,
        )
        answer, sources = (None, [])
        if req.generate_answer:
            answer, sources = _generate_agentic_answer(req.query, result, req.top_k)
        payload = result.model_dump()
        if not req.include_trace:
            payload["trace"] = []
        return RagResponse(**payload, answer=answer, sources=sources)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post(
    "/agentic-ask",
    response_model=AgenticAskResponse,
    include_in_schema=False,
)
def agentic_ask(req: AgenticAskRequest):
    if _main_collection_count is None:
        raise HTTPException(status_code=503, detail="Vector store not ready")
    try:
        result = run_agentic_retrieval(
            req.query,
            max_follow_up_rounds=req.max_follow_up_rounds,
            knowledge_top_k=req.knowledge_top_k,
            case_top_k=req.case_top_k,
            include_trace=True,
        )
        answer, sources = _generate_agentic_answer(req.query, result, req.top_k)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return AgenticAskResponse(
        query=req.query,
        answer=answer,
        sources=sources,
        sufficient=result.sufficient,
        assessment=result.assessment,
        missing_data=result.missing_data,
        conflicts=result.conflicts,
    )
