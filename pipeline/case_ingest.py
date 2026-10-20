"""Build a structured historical-case dataset from the supplied DOCX file.

The case source uses numbered leaf headings such as ``2.1.1`` followed by one
case narrative. Each leaf becomes one JSONL record and one future vector-store
document. Cases remain unverified until a domain expert explicitly confirms
them; unverified cases are never exported as diagnosis evaluation samples.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from docx import Document

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config import CASE_EVAL_FILE, CASE_SOURCE_FILE, CASES_FILE  # noqa: E402


LEAF_HEADING_RE = re.compile(r"^(\d+\.\d+\.\d+)\s*(.+)$")
NUMBERED_HEADING_RE = re.compile(r"^(?:[1-5](?:\.\d+){1,2}|[1-5]\.)\s*\S")

ACTION_PREFIXES = (
    "抢修", "检修", "更换", "整改", "加装", "错峰", "调整", "新增",
    "隔离", "结清", "优化", "修复", "清理", "排空", "吊芯", "试验",
    "经抢修", "供电抢修", "供电人员", "供电紧急调配", "运维", "用户扩容",
    "完工", "当日傍晚", "两小时后", "避雷器改造", "施工方赔付",
)
ACTION_RESULT_RE = re.compile(
    r"(?:后|并|即)(?:恢复|复电|送电|消除|解决|根治|规避|杜绝)|"
    r"(?:故障消除|故障消失|彻底解决|恢复正常|降至0次)$"
)
SCOPE_KEYWORDS = (
    "停电", "全停", "断电", "停产", "停机", "失电", "波及", "限电",
)
RESTORE_KEYWORDS = ("恢复", "复电", "送电")
OBSERVATION_KEYWORDS = (
    "负荷", "电压", "电流", "温度", "告警", "保护", "跳闸", "熔断",
    "短路", "接地", "失压", "离线", "谐波", "绝缘", "发热", "放电",
    "过载", "故障", "黑屏", "积水", "断线", "倒送", "冲击", "碳化",
    "电阻", "老化", "破损", "熔丝", "漏电", "失效", "超标", "带电",
    "刺破", "误挖", "挖断", "啃破", "倒伏", "拉闸",
)


def normalize_text(text: str) -> str:
    """Normalize full-width characters and common unit spelling."""
    text = unicodedata.normalize("NFKC", text).strip()
    text = re.sub(r"(?i)(\d)\s*kv", r"\1kV", text)
    text = text.replace("综合管库", "综合管廊").replace("项管", "顶管")
    text = re.sub(r"\s+", " ", text)
    return text


def read_paragraphs(path: Path) -> list[str]:
    document = Document(str(path))
    return [normalize_text(p.text) for p in document.paragraphs if p.text.strip()]


def parse_case_sections(path: Path) -> list[dict[str, str]]:
    """Return leaf cause headings paired with their following narrative."""
    parsed: list[dict[str, str]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if current and current["parts"]:
            parsed.append({
                "cause_code": current["cause_code"],
                "cause_name": current["cause_name"],
                "case_text": " ".join(current["parts"]),
            })
        current = None

    for paragraph in read_paragraphs(path):
        leaf = LEAF_HEADING_RE.match(paragraph)
        if leaf:
            flush()
            current = {
                "cause_code": leaf.group(1),
                "cause_name": normalize_text(leaf.group(2)),
                "parts": [],
            }
            continue

        if NUMBERED_HEADING_RE.match(paragraph):
            flush()
            continue

        if current is not None:
            current["parts"].append(paragraph)

    flush()
    return parsed


def split_segments(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[,，。；;]+", text) if s.strip()]


def contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def is_action_segment(segment: str) -> bool:
    return segment.startswith(ACTION_PREFIXES) or bool(ACTION_RESULT_RE.search(segment))


def is_scope_segment(segment: str) -> bool:
    return (
        contains_any(segment, SCOPE_KEYWORDS)
        and not contains_any(segment, RESTORE_KEYWORDS)
        and "停电申请" not in segment
    )


def structure_narrative(text: str) -> dict[str, Any]:
    """Extract retrieval-oriented fields without using an external LLM."""
    segments = split_segments(text)
    action_start = next(
        (i for i, segment in enumerate(segments) if is_action_segment(segment)),
        len(segments),
    )

    action_segments = segments[action_start:]
    pre_action = segments[:action_start]
    scope = [
        segment for segment in pre_action
        if is_scope_segment(segment)
    ]
    observations = [
        segment for segment in pre_action
        if contains_any(segment, OBSERVATION_KEYWORDS) and segment not in scope
    ]
    trigger = [
        segment for segment in pre_action
        if segment not in scope and segment not in observations
    ]

    return {
        "trigger": "；".join(trigger),
        "observations": observations,
        "outage_scope": "；".join(scope),
        "action": "；".join(action_segments),
    }


def format_diagnostic_input(case: dict[str, Any]) -> str:
    """Format model input without the cause label or post-event resolution."""
    lines = []
    if case.get("trigger"):
        lines.append(f"事件条件：{case['trigger']}")
    if case.get("observations"):
        lines.append("观测现象：" + "；".join(case["observations"]))
    if case.get("outage_scope"):
        lines.append(f"停电范围：{case['outage_scope']}")
    return "\n".join(lines)


def format_case_content(case: dict[str, Any]) -> str:
    observations = "；".join(case["observations"])
    return "\n".join([
        f"案例编号：{case['case_id']}",
        f"原因分类：{case['cause_code']} {case['cause_name']}",
        f"触发条件：{case['trigger'] or '原始资料未单独说明'}",
        f"观测现象：{observations}",
        f"停电范围：{case['outage_scope'] or '原始资料未单独说明'}",
        f"最终原因：{case['final_cause']}",
        f"处理措施：{case['action'] or '原始资料未单独说明'}",
        f"原始案例：{case['case_text']}",
        f"核验状态：{case['verification_status']}",
    ])


def build_cases(path: Path) -> list[dict[str, Any]]:
    sections = parse_case_sections(path)
    cases: list[dict[str, Any]] = []
    per_code: dict[str, int] = {}

    for chunk_idx, section in enumerate(sections):
        code = section["cause_code"]
        per_code[code] = per_code.get(code, 0) + 1
        case_id = f"CASE-{code}-{per_code[code]:03d}"
        structured = structure_narrative(section["case_text"])
        case: dict[str, Any] = {
            "case_id": case_id,
            "cause_code": code,
            "cause_name": section["cause_name"],
            **structured,
            "final_cause": section["cause_name"],
            "case_text": section["case_text"],
            "source": path.name,
            "verification_status": "待专家确认",
            "evaluation_eligible": False,
            "doc_type": "case",
            "chunk_idx": chunk_idx,
        }
        case["diagnostic_input"] = format_diagnostic_input(case)
        case["content"] = format_case_content(case)
        case["indexed_content"] = "\n".join([
            f"停电原因：{case['cause_code']} {case['cause_name']}",
            case["diagnostic_input"],
            f"案例描述：{case['case_text']}",
        ])
        cases.append(case)

    return cases


def write_jsonl(cases: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def export_verified_eval_cases(cases: list[dict[str, Any]], path: Path) -> int:
    """Export only expert-confirmed cases, with cause labels kept out of input."""
    rows = []
    for case in cases:
        if case.get("verification_status") != "已确认" or not case.get("evaluation_eligible"):
            continue
        rows.append({
            "id": case["case_id"],
            "user_input": case["diagnostic_input"],
            "reference": {
                "cause_code": case["cause_code"],
                "cause_name": case["final_cause"],
            },
            "reference_evidence": [case["case_text"], case["action"]],
            "source": case["source"],
            "exclude_case_ids": [case["case_id"]],
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="将DOCX停电案例拆分为结构化JSONL")
    parser.add_argument("--source", type=Path, default=CASE_SOURCE_FILE)
    parser.add_argument("--output", type=Path, default=CASES_FILE)
    parser.add_argument("--export-eval", action="store_true",
                        help="导出已由专家确认且允许评测的案例")
    parser.add_argument(
        "--eval-only", action="store_true",
        help="不重新解析DOCX，仅从现有案例文件导出评测集",
    )
    parser.add_argument("--eval-output", type=Path, default=CASE_EVAL_FILE)
    args = parser.parse_args()

    source = args.source if args.source.is_absolute() else ROOT / args.source
    output = args.output if args.output.is_absolute() else ROOT / args.output
    eval_output = args.eval_output if args.eval_output.is_absolute() else ROOT / args.eval_output
    if args.eval_only:
        if not output.exists():
            print(f"案例文件不存在：{output}")
            sys.exit(1)
        cases = load_jsonl(output)
        count = export_verified_eval_cases(cases, eval_output)
        print(f"已从现有案例文件导出 {count} 条可评测案例：{eval_output}")
        return

    if not source.exists():
        print(f"案例源文件不存在：{source}")
        sys.exit(1)

    cases = build_cases(source)
    if not cases:
        print("未识别到任何三级原因案例")
        sys.exit(1)

    duplicate_ids = len(cases) != len({case["case_id"] for case in cases})
    if duplicate_ids:
        print("案例编号存在重复，停止写入")
        sys.exit(1)

    write_jsonl(cases, output)
    print(f"已生成 {len(cases)} 条结构化案例：{output}")
    print("核验状态：全部为‘待专家确认’，当前不会进入诊断评测集")

    if args.export_eval:
        count = export_verified_eval_cases(cases, eval_output)
        print(f"已导出 {count} 条可评测案例：{eval_output}")


if __name__ == "__main__":
    main()
