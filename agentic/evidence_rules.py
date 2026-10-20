"""Deterministic evidence requirements and assessment guardrails."""

import re

from agentic.schemas import (
    Evidence,
    EvidenceAssessment,
    EvidenceCoverage,
    RetrievalTask,
)


def _ids(evidence: list[Evidence], predicate) -> list[str]:
    return [item.evidence_id for item in evidence if predicate(item)]


def _add_requirement(
    coverage: list[EvidenceCoverage],
    requirement: str,
    description: str,
    evidence_ids: list[str],
) -> None:
    if any(item.requirement == requirement for item in coverage):
        return
    coverage.append(EvidenceCoverage(
        requirement=requirement,
        description=description,
        satisfied=bool(evidence_ids),
        evidence_ids=evidence_ids,
    ))


def build_coverage(
    query: str,
    tasks: list[RetrievalTask],
    evidence: list[Evidence],
) -> list[EvidenceCoverage]:
    coverage: list[EvidenceCoverage] = []
    knowledge_ids = _ids(evidence, lambda item: item.evidence_type == "knowledge")
    regulation_ids = _ids(evidence, lambda item: item.doc_type == "regulation")
    professional_ids = _ids(
        evidence, lambda item: item.doc_type in {"journal", "thesis"},
    )
    case_ids = _ids(evidence, lambda item: item.evidence_type == "case")
    verified_case_ids = _ids(
        evidence,
        lambda item: item.evidence_type == "case"
        and item.verification_status == "已确认",
    )
    calculation_ids = _ids(
        evidence, lambda item: item.evidence_type == "calculation",
    )

    _add_requirement(
        coverage,
        "evidence",
        "至少取得一条可引用证据",
        [item.evidence_id for item in evidence],
    )

    source_restricted = "仅在来源文件" in query or "指定文档" in query
    if not source_restricted and any(
        term in query for term in ("标准条款", "规程依据", "标准规定", "规范要求")
    ):
        _add_requirement(
            coverage, "regulation", "取得直接的标准或规程证据", regulation_ids,
        )
    if any(term in query for term in ("故障机理", "作用机理", "技术原理")):
        _add_requirement(
            coverage, "professional", "取得论文或专业资料证据", professional_ids,
        )
    if any(term in query for term in ("历史案例", "相似案例", "类似案例")):
        _add_requirement(coverage, "case", "取得历史案例证据", case_ids)
    if any(term in query for term in ("已确认案例", "已核验案例", "真实案例")):
        _add_requirement(
            coverage, "verified_case", "取得经过确认的历史案例", verified_case_ids,
        )

    for task in tasks:
        if task.tool == "search_knowledge" and task.lane == "regulation":
            _add_requirement(
                coverage,
                "regulation",
                "取得直接的标准或规程证据",
                regulation_ids,
            )
        elif task.tool == "search_knowledge" and task.lane == "professional":
            _add_requirement(
                coverage,
                "professional",
                "取得论文或专业资料证据",
                professional_ids,
            )
        elif task.tool == "search_cases":
            _add_requirement(coverage, "case", "取得历史案例证据", case_ids)
        elif task.tool == "search_document" and task.source:
            scoped_ids = _ids(
                evidence,
                lambda item, source=task.source: item.source == source,
            )
            _add_requirement(
                coverage,
                f"document:{task.source}",
                f"在指定来源 {task.source} 内取得证据",
                scoped_ids,
            )
        elif task.tool == "calculate":
            _add_requirement(
                coverage,
                "calculation",
                "使用确定性计算工具完成数值判断",
                calculation_ids,
            )
    if not knowledge_ids and not calculation_ids and not case_ids:
        coverage[0] = coverage[0].model_copy(update={"satisfied": False})
    return coverage


def _follow_up_for_missing(
    missing: list[EvidenceCoverage],
    query: str,
    existing: list[RetrievalTask],
) -> list[RetrievalTask]:
    tasks = list(existing)
    tools_and_lanes = {(task.tool, task.lane) for task in tasks}
    for item in missing:
        if item.requirement == "regulation" and (
            "search_knowledge", "regulation"
        ) not in tools_and_lanes:
            tasks.append(RetrievalTask(
                task_id="RULE-REG",
                goal="补充直接标准规程依据",
                query=query,
                tool="search_knowledge",
                lane="regulation",
            ))
        elif item.requirement == "professional" and (
            "search_knowledge", "professional"
        ) not in tools_and_lanes:
            tasks.append(RetrievalTask(
                task_id="RULE-PRO",
                goal="补充故障机理证据",
                query=query,
                tool="search_knowledge",
                lane="professional",
            ))
        elif item.requirement in {"case", "verified_case"} and (
            "search_cases", "cases"
        ) not in tools_and_lanes:
            tasks.append(RetrievalTask(
                task_id="RULE-CASE",
                goal="补充历史案例证据",
                query=query,
                tool="search_cases",
                lane="cases",
            ))
    return tasks[:4]


def _is_lookup_request(query: str) -> bool:
    return any(term in query for term in ("查找", "检索", "哪些证据"))


def _is_calculation_only(query: str, tasks: list[RetrievalTask]) -> bool:
    return (
        "计算" in query
        and bool(tasks)
        and all(task.tool == "calculate" for task in tasks)
    )


def _declared_business_gaps(query: str) -> list[str]:
    gaps = []
    pattern = re.compile(
        r"(?:没有|未提供|缺少)(.+?)(?=，(?:请|能否|是否|应|可否)|[。？]|$)"
    )
    for match in pattern.finditer(query):
        gap = match.group(1).strip("，；、 ")
        if gap:
            gaps.append(gap)
    if "尚未经过专家确认" in query or "未核验历史案例" in query:
        gaps.append("历史案例的专家确认结果")
    if "能否直接沿用案例原因" in query:
        gaps.append("当前事件与历史案例关键特征的一致性证据")
    return list(dict.fromkeys(gaps))


def enforce_evidence_rules(
    query: str,
    tasks: list[RetrievalTask],
    evidence: list[Evidence],
    assessment: EvidenceAssessment,
) -> tuple[EvidenceAssessment, list[EvidenceCoverage], list[str]]:
    coverage = build_coverage(query, tasks, evidence)
    missing = [item for item in coverage if not item.satisfied]
    issues: list[str] = []

    known_ids = {item.evidence_id for item in evidence}
    cited_ids = set(re.findall(
        r"(?<![A-Za-z0-9_])E\d+(?![A-Za-z0-9_])",
        assessment.reason,
    ))
    unknown_ids = sorted(cited_ids - known_ids)
    if unknown_ids:
        issues.append("模型引用了不存在的证据编号：" + "、".join(unknown_ids))
    missing_citations = assessment.sufficient and bool(evidence) and not cited_ids
    if missing_citations:
        issues.append("模型判断证据充分，但未引用任何 evidence_id")

    has_regulation = any(item.doc_type == "regulation" for item in evidence)
    has_case = any(item.evidence_type == "case" for item in evidence)
    has_calculation = any(item.evidence_type == "calculation" for item in evidence)
    if re.search(r"(?:标准|规程|规范)依据.{0,8}(?:具备|覆盖|充分)", assessment.reason) and not has_regulation:
        issues.append("模型声称标准规程依据已覆盖，但未检出 regulation 证据")
    if re.search(r"案例(?:参考|证据)?.{0,8}(?:具备|覆盖|充分)", assessment.reason) and not has_case:
        issues.append("模型声称案例证据已覆盖，但未检出 case 证据")
    if re.search(r"(?:计算|比例|超出).{0,12}(?:完成|得到|为)", assessment.reason) and not has_calculation:
        issues.append("模型给出数值计算结论，但未检出 calculation 证据")

    lookup_request = _is_lookup_request(query)
    calculation_only = _is_calculation_only(query, tasks)
    declared_gaps = _declared_business_gaps(query)
    matrix_complete = not missing and not unknown_ids
    sufficient = (
        assessment.sufficient
        and matrix_complete
    )
    if calculation_only:
        sufficient = matrix_complete
    if declared_gaps:
        sufficient = False

    follow_up = [] if declared_gaps else _follow_up_for_missing(
        missing, query, assessment.follow_up_tasks,
    )
    if sufficient:
        follow_up = []
    satisfied_names = [item.description for item in coverage if item.satisfied]
    missing_names = [item.description for item in missing]
    if sufficient:
        reason = "证据矩阵校验通过：" + "；".join(satisfied_names) + "。"
    else:
        details = "；".join(missing_names + declared_gaps) or "模型判断当前证据仍不充分"
        reason = "证据矩阵校验未通过：" + details + "。"
    if issues:
        reason += " 校验提示：" + "；".join(issues) + "。"

    missing_data = [] if lookup_request or calculation_only else list(
        assessment.missing_data
    )
    missing_data = list(dict.fromkeys(missing_data + declared_gaps))
    corrected = assessment.model_copy(update={
        "sufficient": sufficient,
        "reason": reason,
        "follow_up_tasks": follow_up,
        "missing_data": missing_data,
    })
    return corrected, coverage, issues
