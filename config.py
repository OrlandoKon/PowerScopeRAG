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
VECTORSTORE_DIR = ROOT / "data" / "vectorstore"

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
QUERY_PREFIX  = "为这个句子生成表示以用于检索相关文章："  # BGE 查询指令前缀（仅查询时加）

# ── 检索 ──────────────────────────────────────────────────────────────────────

DEFAULT_TOP_K = 5   # /ask 和 /retrieve 接口的默认返回数量

# 混合检索 + 精排
RRF_K        = 60                         # RRF 排名常数
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"  # Cross-encoder 精排模型

# ── DeepSeek ──────────────────────────────────────────────────────────────────

DEEPSEEK_API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL    = "deepseek-chat"   # 实际路由到 deepseek-v4-flash

DEFAULT_TEMPERATURE = 1.0
DEFAULT_TOP_P       = 1.0
