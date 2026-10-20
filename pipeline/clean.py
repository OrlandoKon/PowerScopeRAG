"""
pipeline/clean.py — Document-type-aware cleaner for PowerScopeRAG knowledge base.

Input:  doc/regulations/*.md  doc/paper/*.md
Output: data/cleaned/regulations/*.md  data/cleaned/paper/*.md

Three document subtypes
  regulation — GB/DL/GBT national standards
  journal    — peer-reviewed journal articles (contains DOI / 文章编号)
  thesis     — master's dissertations (contains 学位论文 / 导师 header block)

Cleaning strategy per subtype:
  regulation — drop header block before "1 范围", drop 参考文献 tail
  journal    — drop single-line metadata (DOI, 中图分类号…), drop tail from
               收稿日期/作者简介/参考文献, drop pure-English parallel blocks
  thesis     — drop everything before 摘要, drop 参考文献 and 致谢 tails

Common pass (all types): normalise spaced numbers "3 . 1"→"3.1", fix date
artifacts "2008__06__18"→"2008-06-18", remove standalone page numbers,
collapse 3+ blank lines.
"""

import re
import sys
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import DOC_DIR, CLEAN_DIR

DocSubtype = Literal["regulation", "journal", "thesis"]

# ── Subtype detection ─────────────────────────────────────────────────────────

_THESIS_SIGNALS = [
    "学位论文", "硕士", "博士", "导师姓名", "指导教师", "Dissertation", "学 号",
]


def detect_subtype(text: str, rel_dir: str) -> DocSubtype:
    if rel_dir == "regulations":
        return "regulation"
    head = text[:2000]
    if sum(1 for s in _THESIS_SIGNALS if s in head) >= 2:
        return "thesis"
    return "journal"


# ── Compiled patterns ─────────────────────────────────────────────────────────

# Regulation: first numbered main section.
# Handles observed forms (1-2 digit section numbers to avoid matching years like 2021):
#   "1   范围" / "1 范围" / "1范围"  (plain text, various spacing)
#   "1. 范围"  (period after number, DB1331T series)
#   "## 1 总则"  (pymupdf4llm markdown heading)
#   "## **2** 术语" / "## `2` 术语"  (styled digit in markdown)
#   "## 1[范围]"  (brackets around CJK, pymupdf4llm artifact)
_MAIN_SECTION_RE = re.compile(
    r"^(?:#{1,3}\s+)?(?:\*{1,2}|`)?\d{1,2}(?:\*{1,2}|`)?[.\s]*[\[一-鿿（\(]"
)

# State Grid management rules use Chinese chapter/article numbering instead of
# GB-style numeric sections. PDF extraction often places an entire page on one
# line, so these boundaries must be detected in the full text rather than only
# at line starts.
_ZH_NUMBER = "零〇一二三四五六七八九十百千万两"
_CORPORATE_CHAPTER_RE = re.compile(rf"第[{_ZH_NUMBER}0-9]+章")
_CORPORATE_ARTICLE_RE = re.compile(rf"第[{_ZH_NUMBER}0-9]+条")
_CORPORATE_CHAPTER_TITLE_RE = re.compile(
    rf"(?<![一-鿿A-Za-z0-9])"
    rf"(第[{_ZH_NUMBER}0-9]+章)\s*"
    rf"([^。；;\n]{{1,40}}?)"
    rf"(?=\s*第[{_ZH_NUMBER}0-9]+条)"
)
_CORPORATE_ARTICLE_BOUNDARY_RE = re.compile(
    rf"(?<![一-鿿A-Za-z0-9])(第[{_ZH_NUMBER}0-9]+条)(?=\s)"
)
_INLINE_PAGE_NUM_RE = re.compile(r"(?<!\d)-\s*\d{1,3}\s*-(?!\d)")

# 参考文献 heading — handles Markdown, bold, spaced and unspaced forms.
_REF_SECTION_RE = re.compile(
    r"^(?:#{1,6}\s*)?(?:\*{1,2})?参\s*考\s*文\s*献(?:\*{1,2})?\s*[：:﹕]?\s*$"
)

# 前言 / 致谢 / 摘要 headings
_FOREWORD_RE = re.compile(r"^前\s+言\s*$")
_THANKS_RE = re.compile(r"^致\s*谢\s*$")
# Matches: "摘要", "摘  要", "**摘**    **要**", "**摘    要**"
_ABSTRACT_ZH_RE = re.compile(r"^[\s\*]*摘[\s\*]+要[\s\*]*$")

# Journal: single-line metadata drops (match anywhere in line start)
_META_LINE_PATTERNS: list[re.Pattern] = [
    re.compile(r"^文章编号\s*[：:]"),
    re.compile(r"^中图分类号\s*[：:]"),
    re.compile(r"^文献标[志识]码\s*[：:]"),
    re.compile(r"^DOI\s*[：:.]\s*\S", re.IGNORECASE),
    re.compile(r"^收稿日期\s*[：:]"),
    re.compile(r"^基金项目\s*[：:]"),
    re.compile(r"^（本文责任编辑"),
    re.compile(r"^\(本文责任编辑"),
    re.compile(r"^文献引用格式\s*[：:]"),
]

# Journal: combined metadata line where multiple fields appear on one line
# e.g. "中图分类号：TM615        文献标志码：A       文章编号：…"
_META_COMBINED_RE = re.compile(r"中图分类号.{0,5}[：:].{1,30}文献标[志识]码")

# Author bio block start
_AUTHOR_BIO_RE = re.compile(r"^作者简介\s*[：:]")
_INLINE_REF_RE = re.compile(r"参\s*考\s*文\s*献\s*[：:﹕]?")
_REFERENCE_ITEM_RE = re.compile(r"(?m)^\s*-\s*\[[0-9０-９]+\]")

# Orphaned figure / table captions (without actual image)
_FIG_CAPTION_ZH_RE = re.compile(r"^图\s*[\w\d一-鿿．.]+")
_FIG_CAPTION_EN_RE = re.compile(r"^Fig\.?\s", re.IGNORECASE)

# Detects English-parallel lines (no CJK characters, no fullwidth punctuation,
# has at least one ASCII letter, length ≥ 15).  Used to strip English title/
# abstract duplicate blocks in bilingual papers.
_HAS_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")
_HAS_FULLWIDTH_RE = re.compile(r"[＀-￯]")  # Fullwidth （）, 。, ，, ；…


def _is_english_parallel(line: str) -> bool:
    """True if the line is an English-parallel block (no Chinese, no fullwidth, ≥15 chars)."""
    s = line.strip()
    if len(s) < 15:
        return False
    if _HAS_CJK_RE.search(s):
        return False
    if _HAS_FULLWIDTH_RE.search(s):  # Preserves formulas with （）, 。, etc.
        return False
    if not re.search(r"[A-Za-z]", s):  # Must have at least one letter
        return False
    return True

# Standalone page number (1-3 digit line)
_PAGE_NUM_RE = re.compile(r"^\d{1,3}$")
_DECORATED_PAGE_NUM_RE = re.compile(
    r"^[·•∙]\s*[0-9０-９]{1,4}\s*[·•∙]$"
)
_PICTURE_MARKER_RE = re.compile(
    r"(?:"
    r"\*{0,2}\s*[=<\-]*>?\s*picture\s*\[[^\]]*\]\s*"
    r"(?:intentionally\s+)?omitted\s*<?=?[>\-]*\s*\*{0,2}"
    r"|"
    r"\*{0,2}\s*-{2,}\s*(?:start|end)\s+of\s+picture\s+text\s*"
    r"-{2,}\s*\*{0,2}(?:<br\s*/?>)?"
    r")",
    re.IGNORECASE,
)

# Spaced decimal notation:  "3 . 1"  →  "3.1"
_SPACED_NUM_RE = re.compile(r"(\d)\s+\.\s+(\d)")

# Date artifact:  "2008__06__18"  →  "2008-06-18"
_DATE_ARTIFACT_RE = re.compile(r"(\d{4})__(\d{2})__(\d{2})")


# ── Section-boundary helpers ──────────────────────────────────────────────────

def _find_first_main_section(lines: list[str]) -> int:
    """Index of first numbered main section like '1   范围'. Returns -1 if absent."""
    for i, line in enumerate(lines):
        if _MAIN_SECTION_RE.match(line.strip()):
            return i
    return -1


def _find_last(lines: list[str], pattern: re.Pattern) -> int:
    """Last line index matching *pattern* (reverse scan). Returns len(lines)."""
    for i in range(len(lines) - 1, -1, -1):
        if pattern.match(lines[i].strip()):
            return i
    return len(lines)


def _find_first(lines: list[str], pattern: re.Pattern, after: int = 0) -> int:
    """First line index matching *pattern* at or after *after*. Returns len(lines)."""
    for i in range(after, len(lines)):
        if pattern.match(lines[i].strip()):
            return i
    return len(lines)


# ── Common cleaning pass (applied after type-specific pass) ──────────────────

def _fullwidth_to_ascii(text: str) -> str:
    """全角 ASCII 字符（U+FF01–U+FF5E）转半角，全角空格转普通空格。"""
    out = []
    for ch in text:
        cp = ord(ch)
        if 0xFF01 <= cp <= 0xFF5E:
            out.append(chr(cp - 0xFEE0))
        elif cp == 0x3000:  # 全角空格 → 普通空格
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def clean_common(text: str) -> str:
    text = _fullwidth_to_ascii(text)
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        # Standalone page numbers
        if _PAGE_NUM_RE.match(stripped) or _DECORATED_PAGE_NUM_RE.match(stripped):
            continue
        # OCR exports can append an end marker to the same line as a table.
        # Remove only the marker so the recognized values remain searchable.
        line = _PICTURE_MARKER_RE.sub("", line)
        stripped = line.strip()
        if not stripped:
            continue
        # Normalise "3 . 1" → "3.1"
        line = _SPACED_NUM_RE.sub(r"\1.\2", line)
        # Fix date artifact "2008__06__18" → "2008-06-18"
        line = _DATE_ARTIFACT_RE.sub(r"\1-\2-\3", line)
        out.append(line)
    result = "\n".join(out)
    # Collapse 3+ consecutive blank lines → 2
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


# ── Regulation cleaner ────────────────────────────────────────────────────────

def clean_regulation(text: str) -> str:
    """
    Drop everything before the first numbered section ("1   范围") and
    everything from 参考文献 onward.  Also remove orphaned figure captions.
    Returns empty string with a warning comment if no main section is found
    (document is likely a cover-page-only conversion with no technical content).
    """
    chapter = _CORPORATE_CHAPTER_RE.search(text)
    articles = list(_CORPORATE_ARTICLE_RE.finditer(text))
    if chapter and len(articles) >= 2:
        # Drop cover metadata but retain the complete management-rule body.
        text = text[chapter.start():]
        text = _INLINE_PAGE_NUM_RE.sub("\n", text)
        text = _CORPORATE_CHAPTER_TITLE_RE.sub(r"\n# \1 \2\n", text)
        text = _CORPORATE_ARTICLE_BOUNDARY_RE.sub(r"\n## \1\n", text)
        return text

    lines = text.splitlines()
    start = _find_first_main_section(lines)
    if start == -1:
        return ""  # No technical content found (cover page only)
    end = _find_last(lines, _REF_SECTION_RE)
    lines = lines[start:end]

    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        # Short orphaned figure caption (no actual image to accompany it)
        if _FIG_CAPTION_ZH_RE.match(stripped) and len(stripped) < 80:
            continue
        out.append(line)
    return "\n".join(out)


# ── Journal paper cleaner ─────────────────────────────────────────────────────

def _journal_body_end(lines: list[str]) -> int:
    """
    Find where the body ends at the final reference section.

    Author biographies and received-date lines can occur at the bottom of page
    one in multi-page papers. They are removed as metadata, but must never
    truncate the technical sections that continue on the next page.
    """
    return _find_last(lines, _REF_SECTION_RE)


def _strip_inline_reference_tail(text: str) -> str:
    """Cut a reference section whose heading was glued to the previous line."""
    for match in reversed(list(_INLINE_REF_RE.finditer(text))):
        if _REFERENCE_ITEM_RE.search(text[match.end():]):
            return text[:match.start()]
    return text


def _demote_sentence_fragment_heading(line: str) -> str | None:
    """Turn page-break sentence fragments misread as headings back into text."""
    stripped = line.strip()
    match = re.match(r"^#{1,3}\s+(.+?)\s*$", stripped)
    if not match:
        return None
    title = match.group(1).strip()
    if title.startswith(("图", "表")):
        return None
    if title.endswith(("。", "！", "？", "；", ".", "!", "?", ";")):
        return title
    return None


def _append_sentence_fragment(out: list[str], fragment: str) -> None:
    """Reconnect a sentence split by a journal page number and running header."""
    while out and not out[-1].strip():
        out.pop()

    if len(out) >= 2:
        header = out[-1].strip()
        page_index = len(out) - 2
        while page_index >= 0 and not out[page_index].strip():
            page_index -= 1
        page_number = out[page_index].strip() if page_index >= 0 else ""
        likely_running_header = (
            len(header) <= 100
            and not header.startswith("#")
            and not header.endswith(("。", "！", "？", ".", "!", "?"))
            and ("等" in header or re.search(r"[，,].{0,20}$", header))
        )
        if _DECORATED_PAGE_NUM_RE.match(page_number) and likely_running_header:
            del out[page_index:]
            while out and not out[-1].strip():
                out.pop()

    if out:
        previous = out[-1].rstrip()
        if (
            previous
            and not previous.startswith("#")
            and not previous.endswith(("。", "！", "？", "；", ".", "!", "?", ";", "|"))
        ):
            out[-1] = previous + fragment.lstrip()
            return
    out.append(fragment)


def clean_journal(text: str) -> str:
    """
    Drop: single-line metadata, author bio, 参考文献 tail, pure-English
    parallel blocks, orphaned figure captions.
    Keep: 摘要, 关键词, and all numbered body sections.
    """
    text = _strip_inline_reference_tail(text)
    lines = text.splitlines()
    body_end = _journal_body_end(lines)
    lines = lines[:body_end]

    out: list[str] = []
    skip_bio = False  # True while inside 作者简介 block

    for line in lines:
        stripped = line.strip()

        sentence_fragment = _demote_sentence_fragment_heading(line)
        if sentence_fragment is not None:
            _append_sentence_fragment(out, sentence_fragment)
            continue

        # ── End-of-bio block: stop skipping at blank line ──────────────────
        if skip_bio:
            if not stripped:
                skip_bio = False
            continue

        # ── Single-line metadata drops ──────────────────────────────────────
        if any(p.match(stripped) for p in _META_LINE_PATTERNS):
            continue
        if _META_COMBINED_RE.search(stripped):
            continue

        # ── Author bio block ────────────────────────────────────────────────
        if _AUTHOR_BIO_RE.match(stripped):
            skip_bio = True
            continue

        # ── Pure-English parallel blocks (title / abstract duplicates) ─────
        if _is_english_parallel(line):
            continue

        # ── Orphaned figure / table captions ───────────────────────────────
        if _FIG_CAPTION_ZH_RE.match(stripped) and len(stripped) < 100:
            continue
        if _FIG_CAPTION_EN_RE.match(stripped) and len(stripped) < 120:
            continue

        out.append(line)
    return "\n".join(out)


# ── Thesis cleaner ────────────────────────────────────────────────────────────

def clean_thesis(text: str) -> str:
    """
    Drop: school admin header block (everything before 摘要), 参考文献 tail,
    致谢 section, orphaned figure captions.
    Keep: 摘要, 关键词, all body chapters.
    """
    lines = text.splitlines()

    # Start content at 摘要
    start = 0
    for i, line in enumerate(lines):
        if _ABSTRACT_ZH_RE.search(line.strip()):
            start = i
            break

    ref_end = _find_last(lines, _REF_SECTION_RE)
    thanks_end = _find_first(lines, _THANKS_RE, after=start)
    end = min(ref_end, thanks_end)

    lines = lines[start:end]

    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if _FIG_CAPTION_ZH_RE.match(stripped) and len(stripped) < 100:
            continue
        if _FIG_CAPTION_EN_RE.match(stripped) and len(stripped) < 120:
            continue
        # Pure-English lines (English abstract / chapter titles from bilingual thesis)
        if _is_english_parallel(line):
            continue
        out.append(line)
    return "\n".join(out)


# ── File-level orchestration ──────────────────────────────────────────────────

def clean_file(src: Path, dst: Path) -> dict:
    text = src.read_text(encoding="utf-8")
    orig_len = len(text)

    subtype = detect_subtype(text, src.parent.name)

    if subtype == "regulation":
        text = clean_regulation(text)
    elif subtype == "journal":
        text = clean_journal(text)
    else:
        text = clean_thesis(text)

    text = clean_common(text)

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")

    return {"subtype": subtype, "orig": orig_len, "clean": len(text)}


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    sources: list[Path] = []
    for sub in ("regulations", "paper", "datum"):
        d = DOC_DIR / sub
        if d.exists():
            sources.extend(sorted(d.glob("*.md")))

    if not sources:
        print("No .md files found in doc/regulations/, doc/paper/ or doc/datum/")
        sys.exit(1)

    total_orig = total_clean = 0
    for src in sources:
        dst = CLEAN_DIR / src.relative_to(DOC_DIR)
        info = clean_file(src, dst)
        ratio = info["clean"] / info["orig"] * 100 if info["orig"] else 0
        flag = "  ⚠ NO CONTENT" if info["clean"] == 0 else ""
        print(
            f"  [{info['subtype']:10s}]  {src.name:<58s}"
            f"  {info['orig']:>7,} → {info['clean']:>7,} chars  ({ratio:.0f}%){flag}"
        )
        total_orig += info["orig"]
        total_clean += info["clean"]

    overall = total_clean / total_orig * 100 if total_orig else 0
    print(f"\n总计：{total_orig:,} → {total_clean:,} chars  ({overall:.0f}% 保留)")
    print(f"输出目录：{CLEAN_DIR}")


if __name__ == "__main__":
    main()
