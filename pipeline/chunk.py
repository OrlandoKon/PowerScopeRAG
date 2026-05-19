"""
pipeline/chunk.py — Two-stage chunker for PowerScopeRAG knowledge base.

Stage 1: markdownify() injects # headers from numbered-section patterns
Stage 2: MarkdownHeaderTextSplitter creates header-aware chunks
Stage 3: RecursiveCharacterTextSplitter splits chunks that exceed MAX_CHUNK_SIZE

Input:  data/cleaned/regulations/*.md  data/cleaned/paper/*.md
Output: data/chunks.jsonl (one JSON object per line)

Each record:
{
  "content":   "...",
  "source":    "12325-2008-gbt-e-300.md",
  "doc_type":  "regulation",     # regulation | journal | thesis
  "H1":        "4 供电电压偏差的限值",   # top-level heading in chunk scope
  "H2":        "4.2 ...",               # (present only if applicable)
  "H3":        "...",                   # (present only if applicable)
  "chunk_idx": 3                        # 0-based position within source doc
}
"""

import json
import re
import sys
from pathlib import Path
from typing import Literal

from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

ROOT = Path(__file__).parent.parent
CLEAN_DIR = ROOT / "data" / "cleaned"
CHUNKS_FILE = ROOT / "data" / "chunks.jsonl"

# ── Chunking parameters ───────────────────────────────────────────────────────

CHUNK_SIZE = 500        # characters; Chinese is ~1.5× denser than English tokens
CHUNK_OVERLAP = 80
MAX_CHUNK_SIZE = 800    # chunks larger than this are recursively split

# Chinese-aware recursive separators (prefer sentence/clause boundaries)
RECURSIVE_SEPARATORS = ["\n\n", "\n", "。", "；", "，", " ", ""]

# ── Subtype detection (mirrors clean.py) ──────────────────────────────────────

DocSubtype = Literal["regulation", "journal", "thesis"]

_THESIS_SIGNALS = ["学位论文", "硕士", "博士", "导师姓名", "指导教师", "Dissertation"]


def detect_subtype(text: str, rel_dir: str) -> DocSubtype:
    if rel_dir == "regulations":
        return "regulation"
    head = text[:2000]
    if sum(1 for s in _THESIS_SIGNALS if s in head) >= 2:
        return "thesis"
    return "journal"


# ── Markdownification ─────────────────────────────────────────────────────────

# Lines whose title part ends OR contains strong sentence punctuation are content,
# not headings.  _SENT_END_RE matches at the end of the line (also catches list
# items ending with ASCII ";").  _SENT_MID_RE matches anywhere — a title with
# "；" or "。" in the middle is a normative statement, not a section title.
_SENT_END_RE = re.compile(r"[。！？…、，；;]$")
_SENT_MID_RE = re.compile(r"[。！？；;]")

# Pattern A: "**0** **引言**"  or  "**1.2** **title**"
# Both number and title are wrapped in separate ** ** blocks.
_BOLD_NUM_BOLD_TITLE_RE = re.compile(
    r"^\*\*(\d+(?:\.\d+)*)\*\*\s+\*\*([^*]+)\*\*\s*$"
)

# Pattern B: Flexible — handles all cases where the section number (plain or bold)
# is separated from the title by 2+ spaces:
#   "1   范围"
#   "**2.0.4**  间接接触    indirect contact"
#   "3.1  **电器的选择**"
#   "6.1  **一** **般** **规** **定**"
# The (?:\*\*)? before/after the number is optional to handle bold numbers.
_FLEXIBLE_HEADING_RE = re.compile(
    r"^(?:\*\*)?(\d+(?:\.\d+)*)(?:\*\*)?\s{2,}(.+)$"
)

# Pattern C: Standalone section number only on a line — "3.1"  "3. 11"  "3.1 1"
# Uses [. ] character class to tolerate dot-space artifacts ("3. 1" → "3.1").
_STANDALONE_NUM_RE = re.compile(r"^(\d+(?:[. ]\s*\d+)+)\s*$")


def _normalize_num(raw: str) -> str:
    """Normalize '3. 1' → '3.1', '3.1 1' → '3.11' (space artifact from DOCX)."""
    s = re.sub(r"\.\s+", ".", raw.strip())   # "3. 1" → "3.1"
    s = re.sub(r"(\d)\s+(\d)", r"\1\2", s)  # "3.1 1" → "3.11"
    return s


def _heading_depth(num: str) -> int:
    """'1' → 1,  '3.1' → 2,  '4.2.1' → 3 (capped at 3)."""
    return min(num.count(".") + 1, 3)


def _clean_title(raw: str) -> str:
    """Strip ** markers and collapse internal whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"\*+", "", raw)).strip()


def markdownify(text: str) -> str:
    """
    Convert numbered section headings to markdown # syntax so that
    MarkdownHeaderTextSplitter can operate on structure.

    Lines that ARE normative content (end with Chinese sentence punctuation)
    are left unchanged — their section-number prefix stays in the content body.
    """
    lines = text.splitlines()
    out: list[str] = []

    for line in lines:
        s = line.strip()

        # ── Pattern A: "**0** **引言**" ──────────────────────────────────────
        m = _BOLD_NUM_BOLD_TITLE_RE.match(s)
        if m:
            num = _normalize_num(m.group(1))
            title = _clean_title(m.group(2))
            depth = _heading_depth(num)
            out.append("#" * depth + f" {num} {title}")
            continue

        # ── Pattern C: standalone number "3.1"  "3. 11" ─────────────────────
        m = _STANDALONE_NUM_RE.match(s)
        if m:
            num = _normalize_num(m.group(1))
            depth = _heading_depth(num)
            out.append("#" * depth + f" {num}")
            continue

        # ── Pattern B: flexible  "N   title"  "**N**  title"  "N  **title**" ─
        m = _FLEXIBLE_HEADING_RE.match(s)
        if m:
            title_raw = m.group(2)
            num = _normalize_num(m.group(1))
            depth = _heading_depth(num)
            # Reject if the title contains strong sentence punctuation anywhere
            if _SENT_END_RE.search(title_raw) or _SENT_MID_RE.search(title_raw):
                pass
            # For single-digit headings only: also reject if ，or ：appear.
            # Real top-level section titles ("总则", "术语和定义", "布线系统") never
            # contain commas or colons; numbered list items within subsections do.
            elif depth == 1 and ("，" in title_raw or "：" in title_raw):
                pass
            else:
                title = _clean_title(title_raw)
                out.append("#" * depth + f" {num} {title}")
                continue

        out.append(line)

    return "\n".join(out)


# ── Splitters ─────────────────────────────────────────────────────────────────

_md_splitter = MarkdownHeaderTextSplitter(
    headers_to_split_on=[("#", "H1"), ("##", "H2"), ("###", "H3")],
    strip_headers=True,   # heading text goes into metadata, not chunk content
)

_recursive_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=RECURSIVE_SEPARATORS,
    length_function=len,
)


# ── Per-document chunking ─────────────────────────────────────────────────────

def chunk_document(text: str, source: str, subtype: DocSubtype) -> list[dict]:
    """
    Return a list of chunk records for one cleaned document.
    Applies markdownify → MarkdownHeaderTextSplitter → RecursiveCharacterTextSplitter.
    """
    md_text = markdownify(text)
    raw_chunks = _md_splitter.split_text(md_text)

    records: list[dict] = []
    for chunk in raw_chunks:
        content = chunk.page_content.strip()
        if not content:
            continue  # skip empty chunks from consecutive headings

        base_meta: dict = {
            "source": source,
            "doc_type": subtype,
        }
        # Inject only non-empty heading metadata
        for key in ("H1", "H2", "H3"):
            val = chunk.metadata.get(key, "").strip()
            if val:
                base_meta[key] = val

        if len(content) <= MAX_CHUNK_SIZE:
            records.append({"content": content, **base_meta})
        else:
            # Recursively split oversized chunks; all sub-chunks share parent metadata
            for sub in _recursive_splitter.split_text(content):
                sub = sub.strip()
                if sub:
                    records.append({"content": sub, **base_meta})

    # Attach positional index within this document
    for i, rec in enumerate(records):
        rec["chunk_idx"] = i

    return records


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    sources: list[Path] = []
    for sub in ("regulations", "paper"):
        d = CLEAN_DIR / sub
        if d.exists():
            sources.extend(sorted(d.glob("*.md")))

    if not sources:
        print("No cleaned .md files found in data/cleaned/")
        sys.exit(1)

    CHUNKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    total_chunks = 0

    with CHUNKS_FILE.open("w", encoding="utf-8") as fout:
        for src in sources:
            text = src.read_text(encoding="utf-8").strip()
            if not text:
                print(f"  [SKIP empty]  {src.name}")
                continue

            subtype = detect_subtype(text, src.parent.name)
            records = chunk_document(text, src.name, subtype)

            for rec in records:
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")

            print(
                f"  [{subtype:10s}]  {src.name:<58s}  → {len(records):3d} chunks"
            )
            total_chunks += len(records)

    print(f"\n总计：{total_chunks} chunks → {CHUNKS_FILE}")


if __name__ == "__main__":
    main()
