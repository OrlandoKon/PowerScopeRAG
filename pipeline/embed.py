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
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from tqdm import tqdm

ROOT = Path(__file__).parent.parent
CHUNKS_FILE = ROOT / "data" / "chunks.jsonl"
VECTORSTORE_DIR = ROOT / "data" / "vectorstore"

MODEL_NAME = "BAAI/bge-large-zh-v1.5"
COLLECTION_NAME = "powerscoperag"
BATCH_SIZE = 64


def load_chunks() -> list[dict]:
    chunks = []
    with CHUNKS_FILE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def build_collection(chunks: list[dict], reset: bool = False) -> chromadb.Collection:
    VECTORSTORE_DIR.mkdir(parents=True, exist_ok=True)

    ef = SentenceTransformerEmbeddingFunction(
        model_name=MODEL_NAME,
        device="cpu",
        normalize_embeddings=True,
    )

    client = chromadb.PersistentClient(path=str(VECTORSTORE_DIR))

    if reset:
        try:
            client.delete_collection(COLLECTION_NAME)
            print(f"Deleted existing collection '{COLLECTION_NAME}'")
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
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
    documents = [c["content"] for c in chunks]
    metadatas = [
        {k: v for k, v in c.items() if k != "content"}
        for c in chunks
    ]

    print(f"Indexing {len(chunks)} chunks in batches of {BATCH_SIZE}...")
    for start in tqdm(range(0, len(chunks), BATCH_SIZE), unit="batch"):
        end = start + BATCH_SIZE
        collection.add(
            ids=ids[start:end],
            documents=documents[start:end],
            metadatas=metadatas[start:end],
        )

    return collection


def main() -> None:
    reset = "--reset" in sys.argv

    if not CHUNKS_FILE.exists():
        print(f"chunks.jsonl not found at {CHUNKS_FILE}")
        print("Run pipeline/chunk.py first.")
        sys.exit(1)

    chunks = load_chunks()
    print(f"Loaded {len(chunks)} chunks from {CHUNKS_FILE.name}")

    collection = build_collection(chunks, reset=reset)

    total = collection.count()
    print(f"\n向量库就绪：{total} 条文档 → {VECTORSTORE_DIR}")
    print(f"Collection: '{COLLECTION_NAME}'  Model: '{MODEL_NAME}'")


if __name__ == "__main__":
    main()
