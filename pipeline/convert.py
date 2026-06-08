"""
pipeline/convert.py — 多格式文档转 Markdown

将 doc/inbox/{paper,regulations}/ 下的原始文档（.pdf .docx .txt .md）
转换为统一 Markdown，输出到 doc/{paper,regulations}/。

转换工具：
  .pdf  → pymupdf4llm（保留标题层级、表格，原生支持中文）
  .docx → python-docx（与 doc/convert_docx_to_md.py 同一套逻辑）
  .txt  → 内置（检测编号章节，添加标题前缀）
  .md   → 直接复制（不做任何转换）

用法：
    python pipeline/convert.py                    # 处理所有新文件（增量，跳过已有 .md）
    python pipeline/convert.py --force            # 强制重新转换（覆盖已有 .md）
    python pipeline/convert.py --file doc/inbox/regulations/GB50054.pdf  # 单文件
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import DOC_DIR, INBOX_DIR
SUBDIRS = ("regulations", "paper")
SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}


# ── PDF → Markdown ────────────────────────────────────────────────────────────

def pdf_to_md(src: Path) -> str:
    import pymupdf4llm
    return pymupdf4llm.to_markdown(str(src))


# ── DOCX → Markdown ───────────────────────────────────────────────────────────

def _get_run_text(run) -> str:
    text = run.text
    if not text.strip():
        return text
    if run.bold and run.italic:
        return f"***{text}***"
    if run.bold:
        return f"**{text}**"
    if run.italic:
        return f"*{text}*"
    return text


def _get_paragraph_text(para) -> str:
    return "".join(_get_run_text(r) for r in para.runs)


def _is_list_paragraph(para) -> bool:
    from docx.oxml.ns import qn
    style_name = para.style.name if para.style else ""
    if "List" in style_name or "列表" in style_name:
        return True
    pPr = para._p.find(qn("w:pPr"))
    return pPr is not None and pPr.find(qn("w:numPr")) is not None


def _get_list_level(para) -> int:
    from docx.oxml.ns import qn
    try:
        pPr = para._p.find(qn("w:pPr"))
        if pPr is not None:
            numPr = pPr.find(qn("w:numPr"))
            if numPr is not None:
                ilvl = numPr.find(qn("w:ilvl"))
                if ilvl is not None:
                    return int(ilvl.get(qn("w:val"), 0))
    except Exception:
        pass
    return 0


def _is_ordered_list(para) -> bool:
    style_name = para.style.name if para.style else ""
    return "Number" in style_name or "编号" in style_name or "有序" in style_name


def _table_to_md(table) -> str:
    lines = []
    for i, row in enumerate(table.rows):
        cells = []
        seen_tc: set = set()
        for cell in row.cells:
            tc_id = id(cell._tc)
            if tc_id in seen_tc:
                continue
            seen_tc.add(tc_id)
            cells.append(" ".join(cell.text.split()))
        if not cells:
            continue
        lines.append("| " + " | ".join(cells) + " |")
        if i == 0:
            lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
    return "\n".join(lines)


_HEADING_MAP = {
    "Heading 1": "#", "Heading 2": "##", "Heading 3": "###",
    "Heading 4": "####", "Heading 5": "#####",
    "标题 1": "#", "标题 2": "##", "标题 3": "###", "标题 4": "####",
    "Title": "#", "标题": "#",
}


def docx_to_md(src: Path) -> str:
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(str(src))
    md_lines: list[str] = []
    ordered_counters: dict[int, int] = {}

    for block in doc.element.body:
        tag = block.tag.split("}")[-1] if "}" in block.tag else block.tag

        if tag == "tbl":
            for table in doc.tables:
                if table._tbl is block:
                    md_lines += ["", _table_to_md(table), ""]
                    break
            continue

        if tag == "p":
            para = next((p for p in doc.paragraphs if p._p is block), None)
            if para is None:
                continue

            style_name = para.style.name if para.style else "Normal"
            text = _get_paragraph_text(para).strip()

            if not text:
                if md_lines and md_lines[-1] != "":
                    md_lines.append("")
                continue

            if style_name in _HEADING_MAP:
                prefix = _HEADING_MAP[style_name]
                md_lines += ["", f"{prefix} {text}", ""]
                ordered_counters.clear()
                continue

            if _is_list_paragraph(para):
                level = _get_list_level(para)
                indent = "  " * level
                if _is_ordered_list(para):
                    counter = ordered_counters.get(level, 0) + 1
                    ordered_counters[level] = counter
                    for k in list(ordered_counters):
                        if k > level:
                            del ordered_counters[k]
                    md_lines.append(f"{indent}{counter}. {text}")
                else:
                    ordered_counters.clear()
                    md_lines.append(f"{indent}- {text}")
                continue

            ordered_counters.clear()
            md_lines += [text, ""]

    result = "\n".join(md_lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


# ── TXT → Markdown ────────────────────────────────────────────────────────────

# 匹配中文技术文档的编号章节标题，如：
#   "1 范围"  "1.1 定义"  "第一章 绪论"  "一、背景"
_SECTION_RE = re.compile(
    r"^(第[一二三四五六七八九十百]+[章节]|[一二三四五六七八九十]+[、．.]|\d+(\.\d+)*)\s+\S"
)


def _heading_level_from_numbering(heading: str) -> int:
    m = re.match(r"^(\d+(\.\d+)*)", heading.strip())
    if m:
        return min(len(m.group(1).split(".")), 4)
    return 2


def txt_to_md(src: Path) -> str:
    text = src.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if out and out[-1] != "":
                out.append("")
            continue
        if _SECTION_RE.match(stripped):
            level = _heading_level_from_numbering(stripped)
            out += ["", "#" * level + " " + stripped, ""]
        else:
            out.append(stripped)
    result = "\n".join(out)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


# ── 转换分派 ──────────────────────────────────────────────────────────────────

def convert_file(src: Path, dst: Path) -> dict:
    """
    转换单个文件，写入 dst（始终为 .md 后缀）。
    返回 {"status": "ok"|"error", "msg": str}
    """
    suffix = src.suffix.lower()
    try:
        if suffix == ".pdf":
            content = pdf_to_md(src)
        elif suffix == ".docx":
            content = docx_to_md(src)
        elif suffix == ".txt":
            content = txt_to_md(src)
        elif suffix == ".md":
            content = src.read_text(encoding="utf-8")
        else:
            return {"status": "error", "msg": f"不支持的格式：{suffix}"}

        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")
        return {"status": "ok", "msg": f"{len(content):,} chars"}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


def convert_all(force: bool = False) -> dict[str, list]:
    """
    扫描 doc/inbox/{regulations,paper}/ 下所有支持格式文件，
    输出到 doc/{regulations,paper}/。
    增量模式（force=False）跳过目标 .md 已存在的文件。
    返回 {"converted": [...], "skipped": [...], "failed": [...]}
    """
    results: dict[str, list] = {"converted": [], "skipped": [], "failed": []}

    for sub in SUBDIRS:
        inbox_sub = INBOX_DIR / sub
        if not inbox_sub.exists():
            continue
        out_sub = DOC_DIR / sub
        for src in sorted(inbox_sub.iterdir()):
            if src.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            dst = out_sub / (src.stem + ".md")
            if not force and dst.exists():
                results["skipped"].append(src.name)
                continue
            info = convert_file(src, dst)
            if info["status"] == "ok":
                results["converted"].append((src.name, dst.relative_to(ROOT), info["msg"]))
            else:
                results["failed"].append((src.name, info["msg"]))

    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="PowerScopeRAG 文档格式转换")
    parser.add_argument("--force", action="store_true",
                        help="强制重新转换（覆盖已有 .md）")
    parser.add_argument("--file", metavar="PATH",
                        help="只转换指定文件（相对项目根目录或绝对路径）")
    args = parser.parse_args()

    if args.file:
        src = Path(args.file)
        if not src.is_absolute():
            src = ROOT / src
        if not src.exists():
            print(f"文件不存在：{src}")
            sys.exit(1)
        # 推断输出目录：inbox/paper → paper，inbox/regulations → regulations
        try:
            rel = src.relative_to(INBOX_DIR)
            sub = rel.parts[0]
            dst = DOC_DIR / sub / (src.stem + ".md")
        except ValueError:
            dst = src.with_suffix(".md")

        if not args.force and dst.exists():
            print(f"已存在，跳过（--force 可强制覆盖）：{dst.relative_to(ROOT)}")
            sys.exit(0)

        info = convert_file(src, dst)
        if info["status"] == "ok":
            print(f"  ✓  {src.name}  →  {dst.relative_to(ROOT)}  ({info['msg']})")
        else:
            print(f"  ✗  {src.name}  失败：{info['msg']}")
            sys.exit(1)
        return

    print(f"\nPowerScopeRAG 文档转换  |  inbox: {INBOX_DIR.relative_to(ROOT)}")
    print("=" * 60)

    r = convert_all(force=args.force)

    for name, dst, msg in r["converted"]:
        print(f"  ✓  {name:<50s}  →  {dst}  ({msg})")
    for name in r["skipped"]:
        print(f"  –  {name:<50s}  已存在，跳过")
    for name, msg in r["failed"]:
        print(f"  ✗  {name:<50s}  失败：{msg}")

    print(f"\n完成：{len(r['converted'])} 转换，{len(r['skipped'])} 跳过，{len(r['failed'])} 失败")
    if r["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
