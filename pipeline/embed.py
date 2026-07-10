"""
pipeline/embed.py — Embed chunks and index into Chroma vector store.

Input:  data/chunks.jsonl
Output: data/vectorstore/  (Chroma persistent collection "powerscoperag")

Embedding model: BAAI/bge-large-zh-v1.5 via sentence-transformers.
  - 1024-dim dense vectors, strong on Chinese technical retrieval
  - Instruction prefix "为这个句子生成表示以用于检索相关文章：" applied to queries
    at retrieval time (not at indexing time — documents are indexed as-is).

Each Chroma document:
  id       = "{source}::{chunk_idx}"
  document = chunk["content"]
  metadata = all remaining fields (source, doc_type, H1, H2, H3, chunk_idx)
"""

import json
import sys
import argparse
from pathlib import Path
from typing import Any

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from tqdm import tqdm

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import (
    CHUNKS_FILE,
    DOC2QUERY_CHUNKS_FILE,
    VECTORSTORE_DIR,
    EMBED_MODEL,
    EMBED_BATCH_SIZE,
    COLLECTION_NAME,
    DOC2QUERY_COLLECTION_NAME,
    LOCAL_MODEL_DEVICE,
)


def resolve_device() -> str:
    if LOCAL_MODEL_DEVICE != "auto":
        return LOCAL_MODEL_DEVICE
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def load_chunks(path: Path) -> list[dict]:
    chunks = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def _metadata_for_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for k, v in chunk.items():
        if k in {"content", "indexed_content"}:
            continue
        if isinstance(v, (list, dict)):
            metadata[k] = json.dumps(v, ensure_ascii=False)
        elif v is not None:
            metadata[k] = v
    if "indexed_content" in chunk:
        metadata["original_content"] = chunk["content"]
    return metadata


def build_collection(
    chunks: list[dict],
    reset: bool = False,
    collection_name: str = COLLECTION_NAME,
) -> chromadb.Collection:
    VECTORSTORE_DIR.mkdir(parents=True, exist_ok=True)
    device = resolve_device()
    print(f"Embedding device: {device}")

    ef = SentenceTransformerEmbeddingFunction(
        model_name=EMBED_MODEL,
        device=device,
        normalize_embeddings=True,
    )

    client = chromadb.PersistentClient(path=str(VECTORSTORE_DIR))

    if reset:
        try:
            client.delete_collection(collection_name)
            print(f"Deleted existing collection '{collection_name}'")
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=collection_name,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    existing_ids = set(collection.get(include=[])["ids"])
    if existing_ids:
        print(f"Collection already has {len(existing_ids)} documents.")
        new_chunks = [c for c in chunks if f"{c['source']}::{c['chunk_idx']}" not in existing_ids]
        if not new_chunks:
            print("All chunks already indexed. Use --reset to re-index.")
            return collection
        print(f"Adding {len(new_chunks)} new chunks...")
        chunks = new_chunks

    ids = [f"{c['source']}::{c['chunk_idx']}" for c in chunks]
    documents = [c.get("indexed_content", c["content"]) for c in chunks]
    metadatas = [_metadata_for_chunk(c) for c in chunks]

    print(f"Indexing {len(chunks)} chunks in batches of {EMBED_BATCH_SIZE}...")
    for start in tqdm(range(0, len(chunks), EMBED_BATCH_SIZE), unit="batch"):
        end = start + EMBED_BATCH_SIZE
        collection.add(
            ids=ids[start:end],
            documents=documents[start:end],
            metadatas=metadatas[start:end],
        )

    return collection


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed chunks into Chroma vector store.")
    parser.add_argument("--reset", action="store_true", help="Delete and rebuild target collection")
    parser.add_argument("--doc2query", action="store_true",
                        help="Index data/chunks_doc2query.jsonl into powerscoperag_doc2query")
    parser.add_argument("--chunks", type=Path, help="Override chunks jsonl path")
    parser.add_argument("--collection", help="Override collection name")
    args = parser.parse_args()

    chunks_file = args.chunks or (DOC2QUERY_CHUNKS_FILE if args.doc2query else CHUNKS_FILE)
    collection_name = args.collection or (
        DOC2QUERY_COLLECTION_NAME if args.doc2query else COLLECTION_NAME
    )

    if not chunks_file.exists():
        print(f"chunks file not found at {chunks_file}")
        print("Run pipeline/chunk.py first.")
        sys.exit(1)

    chunks = load_chunks(chunks_file)
    print(f"Loaded {len(chunks)} chunks from {chunks_file.name}")

    collection = build_collection(chunks, reset=args.reset, collection_name=collection_name)

    total = collection.count()
    print(f"\n向量库就绪：{total} 条文档 → {VECTORSTORE_DIR}")
    print(f"Collection: '{collection_name}'  Model: '{EMBED_MODEL}'")


if __name__ == "__main__":
    main()
