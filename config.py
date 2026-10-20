"""
config.py — 全局超参数与路径配置

所有 pipeline 文件从此处导入，避免参数分散在各处。
DEEPSEEK_API_KEY 通过环境变量注入（systemd 服务已配置），本地开发可在 .env 文件中设置。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── 路径 ──────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent
DOC_DIR        = ROOT / "doc"
INBOX_DIR      = DOC_DIR / "inbox"
CLEAN_DIR      = ROOT / "data" / "cleaned"
CHUNKS_FILE    = ROOT / "data" / "chunks.jsonl"
DOC2QUERY_FILE = ROOT / "data" / "doc2query.jsonl"
DOC2QUERY_CHUNKS_FILE = ROOT / "data" / "chunks_doc2query.jsonl"
CASE_SOURCE_DIR = DOC_DIR / "cases" / "raw"
CASE_SOURCE_FILE = CASE_SOURCE_DIR / "配网故障分析.docx"
CASES_FILE = ROOT / "data" / "cases.jsonl"
CASE_EVAL_FILE = ROOT / "tests" / "case_eval_cases.json"
VECTORSTORE_DIR = Path(os.environ.get(
    "POWERSCOPE_VECTORSTORE_DIR",
    str(ROOT / "data" / "vectorstore_v2"),
))

# ── 分块参数 ──────────────────────────────────────────────────────────────────

CHUNK_SIZE   = 500   # 目标分块字符数（中文约 1.5× 英文 token 密度）
CHUNK_OVERLAP = 80   # 相邻分块重叠字符数
MAX_CHUNK_SIZE = 800  # 超过此值的块进行递归二次切分

# 中文优先的递归切分分隔符（优先句子/子句边界）
RECURSIVE_SEPARATORS = ["\n\n", "\n", "。", "；", "，", " ", ""]

# ── 向量化 ────────────────────────────────────────────────────────────────────

EMBED_MODEL   = "BAAI/bge-large-zh-v1.5"   # 中文技术检索推荐模型
EMBED_BATCH_SIZE = 64                        # 每批向量化的分块数
COLLECTION_NAME  = "powerscoperag"           # Chroma 集合名称
STRUCTURED_COLLECTION_NAME = "powerscoperag_structured_v4"
DOC2QUERY_COLLECTION_NAME = "powerscoperag_doc2query"
CASE_COLLECTION_NAME = "powerscoperag_cases"
DEFAULT_RETRIEVAL_MODE = os.environ.get(
    "POWERSCOPE_RETRIEVAL_MODE",
    "hybrid+rerank+hierarchical",
)
if DEFAULT_RETRIEVAL_MODE.endswith(("+structured", "+hierarchical")):
    ACTIVE_COLLECTION_NAME = STRUCTURED_COLLECTION_NAME
elif DEFAULT_RETRIEVAL_MODE.endswith("+doc2query"):
    ACTIVE_COLLECTION_NAME = DOC2QUERY_COLLECTION_NAME
else:
    ACTIVE_COLLECTION_NAME = COLLECTION_NAME
QUERY_PREFIX  = "为这个句子生成表示以用于检索相关文章："  # BGE 查询指令前缀（仅查询时加）

# 本地 embedding / reranker 运行设备。默认 auto：CUDA 可用则用 GPU，否则 CPU。
# 可通过环境变量覆盖：POWERSCOPE_DEVICE=cuda / cpu
LOCAL_MODEL_DEVICE = os.environ.get("POWERSCOPE_DEVICE", "auto")

# ── 检索 ──────────────────────────────────────────────────────────────────────

DEFAULT_TOP_K = 5   # /ask 和 /retrieve 接口的默认返回数量

# 混合检索 + 精排
RRF_K        = 60                         # RRF 排名常数
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"  # Cross-encoder 精排模型

# ── DeepSeek ──────────────────────────────────────────────────────────────────

DEEPSEEK_API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = os.environ.get(
    "DEEPSEEK_MODEL",
    "deepseek-flash",
)  # DeepSeek-V4.1-Flash official API identifier

DEFAULT_TEMPERATURE = 1.0
DEFAULT_TOP_P       = 1.0
