"""LLM-driven retrieval planning constrained by a fixed knowledge-tool schema."""

import json
import re

from pydantic import ValidationError

from agentic.llm import complete_json
from agentic.schemas import RetrievalPlan, RetrievalTask


_PLANNER_PROMPT = """你是400V低压配电网知识检索规划器，只规划取证任务，不判断最终停电原因。

将输入拆成1至4个彼此互补、可独立执行的任务。可用工具只有：
- search_knowledge：现有混合召回、交叉编码精排和层级检索，配合lane选择知识通道
- search_cases：历史案例检索，lane固定为cases
- search_document：仅当用户明确给出来源文件时，在该文档内检索，必须填写source
- calculate：仅当输入已经提供两个可比较数值时使用，执行确定性比较或比例计算

search_knowledge可用知识通道：
- regulation：标准、规程、技术阈值和操作要求
- professional：论文、专业资料、故障机理和分析方法
- all：无法预先确定资料类型时使用

要求：
1. 查询应保留设备对象、故障现象、保护动作和关键数值，不要写文件名。
2. 不得虚构输入中没有的事实；不确定的信息写入missing_data。
3. calculate的calculation结构包含operation（compare或ratio）、left_value、right_value、left_label、right_label和unit。
4. 使用完成任务所需的最少工具。用户只要求数值计算时，只生成calculate；用户明确只查案例时，只生成search_cases；已有明确知识通道时不要再增加all通道的重复检索。
5. 普通知识问答优先用一次search_knowledge；只有问题明确需要跨来源取证时才拆成多个检索任务。不要为了保底重复检索原始问题。
6. 论文或标准中描述的“案例”仍属该文档内容；只有用户明确要求历史、相似或独立案例时才调用search_cases。
7. ratio会同时返回占基准比例和超出基准百分比；compare会同时返回大小关系、绝对差值和相对超限百分比。同一组操作数不要同时生成ratio和compare，除非用户同时明确要求比例和绝对差值。
8. 只输出JSON对象，结构为：
{"known_facts":["..."],"missing_data":["..."],"tasks":[{"task_id":"T1","goal":"...","query":"...","tool":"search_knowledge","lane":"regulation","source":null,"calculation":null}]}。
"""


def _normalize_tasks(raw: dict) -> dict:
    normalized = dict(raw)
    tasks = []
    tool_aliases = {
        "knowledge": "search_knowledge",
        "search": "search_knowledge",
        "case": "search_cases",
        "cases": "search_cases",
        "document": "search_document",
        "calculation": "calculate",
    }
    lane_aliases = {
        "case": "cases",
        "standard": "regulation",
        "standards": "regulation",
        "rule": "regulation",
        "rules": "regulation",
        "paper": "professional",
        "papers": "professional",
    }
    for field in ("tasks", "follow_up_tasks"):
        tasks = []
        for item in raw.get(field, [])[:4]:
            if not isinstance(item, dict):
                continue
            task = dict(item)
            raw_tool = task.get("tool") or "search_knowledge"
            task["tool"] = tool_aliases.get(raw_tool, raw_tool)
            raw_lane = task.get("lane")
            task["lane"] = lane_aliases.get(raw_lane, raw_lane) or (
                "cases" if task["tool"] == "search_cases" else "all"
            )
            calculation = task.get("calculation")
            if isinstance(calculation, dict):
                calculation = dict(calculation)
                if calculation.get("operation") in {"subtract", "difference"}:
                    calculation["operation"] = "compare"
                task["calculation"] = calculation
            tasks.append(task)
        if field in raw:
            normalized[field] = tasks
    return normalized


def _constrain_initial_tasks(
    query: str,
    tasks: list[RetrievalTask],
) -> list[RetrievalTask]:
    tasks = [
        task if task.tool != "search_document" or (
            task.source in query
            and task.source.lower().endswith((".md", ".pdf", ".docx", ".txt"))
        ) else task.model_copy(update={
            "tool": "search_knowledge",
            "lane": "all",
            "source": None,
            "query": query[:500],
        })
        for task in tasks
    ]
    has_case_request = requests_case_evidence(query)
    has_other_knowledge_request = any(
        term in query for term in ("标准", "规程", "规范", "机理", "原理", "依据")
    )
    explicit_case_only = (
        bool(re.search(r"请用.{0,12}案例", query))
        or "两个未核验历史案例" in query
    )
    if explicit_case_only and not has_other_knowledge_request:
        case_tasks = [task for task in tasks if task.tool == "search_cases"]
        if case_tasks:
            tasks = case_tasks

    if not has_case_request:
        non_case_tasks = [task for task in tasks if task.tool != "search_cases"]
        tasks = non_case_tasks or [RetrievalTask(
            task_id="T1",
            goal="查找问题所需的知识依据",
            query=query[:500],
            tool="search_knowledge",
            lane="all",
        )]

    if "治理" in query and not any(
        term in query for term in ("标准", "规程", "规范", "条款", "GB/T", "DL/T")
    ):
        tasks = [
            task.model_copy(update={"lane": "all"})
            if task.tool == "search_knowledge" and task.lane == "regulation"
            else task
            for task in tasks
        ]

    return tasks


def requests_case_evidence(query: str) -> bool:
    return bool(re.search(r"请用.{0,12}案例", query)) or any(
        term in query for term in (
        "历史案例", "相似案例", "类似案例", "相关案例", "治理案例",
        "处置案例", "已确认案例", "已核验案例", "真实案例", "案例库",
        "查找案例", "检索案例", "有无案例", "案例参考", "沿用案例原因",
        )
    )


def _expand_search_query(
    original_query: str,
    task: RetrievalTask,
) -> RetrievalTask:
    if task.tool not in {"search_knowledge", "search_cases"}:
        return task
    compact_original = "".join(original_query.split())
    compact_task = "".join(task.query.split())
    if compact_original in compact_task:
        return task
    suffix = f"；原始问题：{original_query}"
    expanded = task.query + suffix[:max(0, 500 - len(task.query))]
    return task.model_copy(update={"query": expanded})


def plan_retrieval(query: str) -> RetrievalPlan:
    raw = _normalize_tasks(complete_json(_PLANNER_PROMPT, query))
    plan = RetrievalPlan.model_validate(raw)
    constrained = _constrain_initial_tasks(query, plan.tasks)
    tasks = []
    for index, task in enumerate(constrained[:4], 1):
        if (len(constrained) == 1 and task.tool == "search_knowledge"
                and len(query) <= 500):
            task = task.model_copy(update={"query": query})
        else:
            task = _expand_search_query(query, task)
        tasks.append(task.model_copy(update={"task_id": f"T{index}"}))
    return plan.model_copy(update={"tasks": tasks})


_FOLLOW_UP_PROMPT = """你是400V低压配电网RAG证据评估器。

根据原问题、已执行任务和检索证据，判断知识证据是否足以支持后续多智能体研判。重点检查：
1. 证据是否直接相关并覆盖关键对象、条件或数值；
2. 是否同时具备必要的规范依据、故障机理或案例参考；
3. 来源之间是否存在冲突；
4. 缺少的是知识证据，还是只能由当前事件量测、拓扑或现场核查提供的数据。
5. answer_evidence_ids只列能直接回答原问题的证据编号，按价值排序，最多5个；目录、背景和仅提到同一主题的片段不要列入。无法直接回答时返回空数组。

可用工具为search_knowledge、search_cases、search_document和calculate：
- 已找到正确来源但片段不完整时，优先使用search_document，source必须严格复制现有证据中的来源名称；
- 判断涉及两个已知数值的大小、比例或超限幅度时，必须使用calculate，不得自行心算；
- 只有缺少知识证据时才继续检索，缺少业务数据时写入missing_data。

follow_up_tasks最多4个，不得反复使用已经执行过的查询。
只输出JSON对象，结构为：
{"sufficient":true,"reason":"...","missing_data":["..."],"conflicts":["..."],"answer_evidence_ids":["E1"],"follow_up_tasks":[{"task_id":"F1","goal":"...","query":"...","tool":"search_document","lane":"all","source":"来源.md","calculation":null}]}。
"""


def assessment_payload(
    query: str,
    tasks: list[dict],
    evidence: list[dict],
) -> str:
    compact_evidence = [
        {
            "evidence_id": item["evidence_id"],
            "evidence_type": item["evidence_type"],
            "source": item["source"],
            "section": item.get("section"),
            "verification_status": item.get("verification_status"),
            "content": item["content"][:1200],
        }
        for item in evidence
    ]
    return json.dumps(
        {"query": query, "executed_tasks": tasks, "evidence": compact_evidence},
        ensure_ascii=False,
    )


def assess_evidence(query: str, tasks: list[dict], evidence: list[dict]):
    from agentic.schemas import EvidenceAssessment

    payload = assessment_payload(query, tasks, evidence)
    raw = _normalize_tasks(complete_json(_FOLLOW_UP_PROMPT, payload))
    try:
        return EvidenceAssessment.model_validate(raw)
    except ValidationError:
        repair_prompt = (
            _FOLLOW_UP_PROMPT
            + "\n上一份JSON不符合工具结构。请重新输出完整JSON；"
            "calculate任务的calculation必须是含operation、left_value、"
            "right_value等字段的对象，不能是算式字符串。"
        )
        repaired = _normalize_tasks(complete_json(
            repair_prompt,
            payload + "\n上一份不合格JSON：" + json.dumps(raw, ensure_ascii=False),
        ))
        try:
            return EvidenceAssessment.model_validate(repaired)
        except ValidationError:
            valid_tasks = []
            for task in repaired.get("follow_up_tasks", []):
                try:
                    valid_tasks.append(RetrievalTask.model_validate(task))
                except ValidationError:
                    continue
            repaired["follow_up_tasks"] = [
                task.model_dump() for task in valid_tasks
            ]
            repaired["sufficient"] = False
            repaired["reason"] = (
                str(repaired.get("reason", ""))
                + "；部分补查任务格式无效，未执行"
            )
            return EvidenceAssessment.model_validate(repaired)
