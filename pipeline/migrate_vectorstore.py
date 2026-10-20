"""Rebuild a staged Chroma store by reusing compatible stored embeddings.

The current JSONL manifests are the source of truth for documents and metadata.
Only embeddings whose stored document text still matches are copied, so a
changed chunk can never silently receive a stale vector.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import (  # noqa: E402
    CHUNKS_FILE,
    COLLECTION_NAME,
    DOC2QUERY_CHUNKS_FILE,
    DOC2QUERY_COLLECTION_NAME,
    EMBED_MODEL,
    VECTORSTORE_DIR,
)


def load_chunks(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def metadata_for_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key, value in chunk.items():
        if key in {"content", "indexed_content"}:
            continue
        if isinstance(value, (list, dict)):
            metadata[key] = json.dumps(value, ensure_ascii=False)
        elif value is not None:
            metadata[key] = value
    if "indexed_content" in chunk:
        metadata["original_content"] = chunk["content"]
    return metadata


def read_existing(collection: chromadb.Collection, batch_size: int) -> dict[str, tuple[str, Any]]:
    rows: dict[str, tuple[str, Any]] = {}
    for offset in range(0, collection.count(), batch_size):
        batch = collection.get(
            limit=batch_size,
            offset=offset,
            include=["documents", "embeddings"],
        )
        for item_id, document, embedding in zip(
            batch["ids"], batch["documents"], batch["embeddings"], strict=True,
        ):
            rows[item_id] = (document, embedding)
    return rows


def migrate_collection(
    source_client: chromadb.ClientAPI,
    target_client: chromadb.ClientAPI,
    collection_name: str,
    chunks_file: Path,
    batch_size: int,
    embedding_function: SentenceTransformerEmbeddingFunction,
) -> tuple[int, int]:
    chunks = load_chunks(chunks_file)
    source = source_client.get_collection(collection_name)
    stored = read_existing(source, batch_size)

    ids = [f"{chunk['source']}::{chunk['chunk_idx']}" for chunk in chunks]
    documents = [chunk.get("indexed_content", chunk["content"]) for chunk in chunks]
    missing = [item_id for item_id in ids if item_id not in stored]
    mismatched = [
        item_id
        for item_id, document in zip(ids, documents, strict=True)
        if item_id in stored and stored[item_id][0] != document
    ]
    if missing or mismatched:
        details = []
        if missing:
            details.append(f"{len(missing)} missing embeddings")
        if mismatched:
            details.append(f"{len(mismatched)} changed documents")
        raise RuntimeError(
            f"Cannot safely migrate '{collection_name}': {', '.join(details)}. "
            "Re-embed the affected collection instead."
        )

    try:
        target_client.delete_collection(collection_name)
    except Exception:
        pass
    target = target_client.create_collection(
        collection_name,
        embedding_function=embedding_function,
        metadata={"hnsw:space": "cosine"},
    )

    metadatas = [metadata_for_chunk(chunk) for chunk in chunks]
    embeddings = [stored[item_id][1] for item_id in ids]
    for start in range(0, len(chunks), batch_size):
        end = start + batch_size
        target.add(
            ids=ids[start:end],
            documents=documents[start:end],
            metadatas=metadatas[start:end],
            embeddings=embeddings[start:end],
        )

    removed = source.count() - target.count()
    return target.count(), removed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="按当前JSONL清单复用旧向量，构建新的Chroma向量库",
    )
    parser.add_argument(
        "--source-dir", type=Path, default=ROOT / "data" / "vectorstore",
        help="已有向量库目录",
    )
    parser.add_argument(
        "--target-dir", type=Path, default=VECTORSTORE_DIR,
        help="新向量库目录",
    )
    parser.add_argument("--batch-size", type=int, default=256)
    args = parser.parse_args()

    source_dir = args.source_dir.resolve()
    target_dir = args.target_dir.resolve()
    if source_dir == target_dir:
        parser.error("source-dir and target-dir must be different")
    if args.batch_size < 1:
        parser.error("batch-size must be positive")

    target_dir.mkdir(parents=True, exist_ok=True)
    source_client = chromadb.PersistentClient(path=str(source_dir))
    target_client = chromadb.PersistentClient(path=str(target_dir))
    embedding_function = SentenceTransformerEmbeddingFunction(
        model_name=EMBED_MODEL,
        device="cpu",
        normalize_embeddings=True,
    )

    specs = (
        (COLLECTION_NAME, CHUNKS_FILE),
        (DOC2QUERY_COLLECTION_NAME, DOC2QUERY_CHUNKS_FILE),
    )
    for collection_name, chunks_file in specs:
        count, removed = migrate_collection(
            source_client,
            target_client,
            collection_name,
            chunks_file,
            args.batch_size,
            embedding_function,
        )
        print(f"{collection_name}: {count} 条，移除旧记录 {removed} 条")

    print(f"迁移完成：{target_dir}")


if __name__ == "__main__":
    main()
