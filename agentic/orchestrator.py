"""Bounded plan, retrieve, assess, and retry workflow."""

import re
from collections.abc import Callable

from config import DEEPSEEK_MODEL

from agentic.evidence_rules import enforce_evidence_rules
from agentic.planner import assess_evidence, plan_retrieval, requests_case_evidence
from agentic.schemas import (
    AgenticRetrievalResult,
    AnswerSlot,
    Evidence,
    EvidenceAssessment,
    EvidenceCoverage,
    RetrievalPlan,
    RetrievalTask,
    TraceStep,
)
from agentic.tools import execute_retrieval_task


Planner = Callable[[str], RetrievalPlan]
Assessor = Callable[[str, list[dict], list[dict]], EvidenceAssessment]
Executor = Callable[[RetrievalTask, int, int], list[dict]]


def _evidence_key(hit: dict) -> tuple:
    if hit.get("evidence_type") == "case":
        return ("case", hit.get("case_id"), hit.get("source"))
    if hit.get("evidence_type") == "calculation":
        return ("calculation", hit.get("chunk_idx"))
    return ("knowledge", hit.get("source"), str(hit.get("chunk_idx")))


def _score(hit: dict) -> float | None:
    for field in ("hierarchical_score", "rerank_score", "rrf_score"):
        if field in hit:
            return float(hit[field])
    if "distance" in hit:
        return -float(hit["distance"])
    return None


def _to_evidence(hit: dict, task_id: str, evidence_id: str) -> Evidence:
    section = " > ".join(
        str(hit[level]) for level in ("H1", "H2", "H3") if hit.get(level)
    ) or None
    return Evidence(
        evidence_id=evidence_id,
        task_id=task_id,
        evidence_type=hit.get("evidence_type", "knowledge"),
        source=str(hit.get("source", "")),
        content=str(hit.get("content", hit.get("case_text", ""))),
        doc_type=hit.get("doc_type"),
        section=section,
        chunk_idx=hit.get("chunk_idx"),
        case_id=hit.get("case_id"),
        verification_status=hit.get("verification_status"),
        context_format=hit.get("context_format"),
        distance=hit.get("distance"),
        score=_score(hit),
        calculation=hit.get("calculation"),
    )


def _merge_unique(left: list[str], right: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in left + right if item.strip()))


def _context_contains_number(value: float, context: str) -> bool:
    rendered = format(value, ".12g")
    pattern = rf"(?<![\d.]){re.escape(rendered)}(?:0+)?(?![\d.])"
    return bool(re.search(pattern, context))


def _task_is_grounded(
    task: RetrievalTask,
    original_query: str,
    evidence: list[Evidence],
    follow_up: bool,
) -> bool:
    if task.tool == "search_document" and follow_up:
        known_sources = {item.source for item in evidence}
        if task.source not in known_sources:
            return False
    if task.tool == "calculate":
        context = original_query + "\n" + "\n".join(item.content for item in evidence)
        spec = task.calculation
        return _context_contains_number(
            spec.left_value, context,
        ) and _context_contains_number(spec.right_value, context)
    return True


def run_agentic_retrieval(
    query: str,
    *,
    max_follow_up_rounds: int = 1,
    knowledge_top_k: int = 4,
    case_top_k: int = 3,
    include_trace: bool = True,
    planner: Planner = plan_retrieval,
    assessor: Assessor = assess_evidence,
    executor: Executor = execute_retrieval_task,
) -> AgenticRetrievalResult:
    if not 0 <= max_follow_up_rounds <= 2:
        raise ValueError("max_follow_up_rounds must be between 0 and 2")

    plan = planner(query)
    pending = plan.tasks
    executed_queries: set[tuple[str, str]] = set()
    all_tasks: list[RetrievalTask] = []
    evidence: list[Evidence] = []
    evidence_keys: set[tuple] = set()
    trace: list[TraceStep] = []
    assessment = EvidenceAssessment(
        sufficient=False,
        reason="尚未评估检索证据",
        missing_data=plan.missing_data,
    )
    coverage: list[EvidenceCoverage] = []
    validation_issues: list[str] = []

    for round_index in range(max_follow_up_rounds + 1):
        round_tasks = []
        for task in pending:
            if task.tool == "search_cases" and not requests_case_evidence(query):
                continue
            if not _task_is_grounded(
                task,
                original_query=query,
                evidence=evidence,
                follow_up=round_index > 0,
            ):
                continue
            query_key = (
                task.tool,
                task.lane,
                task.source or "",
                "".join(task.query.lower().split()),
                str(task.calculation.model_dump() if task.calculation else ""),
            )
            if query_key in executed_queries:
                continue
            executed_queries.add(query_key)
            round_tasks.append(task)

        if not round_tasks:
            break

        for task in round_tasks:
            hits = executor(task, knowledge_top_k, case_top_k)
            new_ids = []
            for hit in hits:
                key = _evidence_key(hit)
                if key in evidence_keys:
                    continue
                evidence_keys.add(key)
                evidence_id = f"E{len(evidence) + 1}"
                evidence.append(_to_evidence(hit, task.task_id, evidence_id))
                new_ids.append(evidence_id)
            trace.append(
                TraceStep(round=round_index + 1, task=task, evidence_ids=new_ids)
            )
        all_tasks.extend(round_tasks)

        assessment = assessor(
            query,
            [task.model_dump() for task in all_tasks],
            [item.model_dump() for item in evidence],
        )
        assessment, coverage, validation_issues = enforce_evidence_rules(
            query,
            all_tasks,
            evidence,
            assessment,
        )
        if assessment.sufficient or round_index >= max_follow_up_rounds:
            break
        pending = [
            task.model_copy(update={"task_id": f"R{round_index + 2}T{i}"})
            for i, task in enumerate(assessment.follow_up_tasks, 1)
        ][:4]

    known_ids = {item.evidence_id for item in evidence}
    answer_slots = [
        AnswerSlot(
            question_part=slot.question_part,
            evidence_ids=[item_id for item_id in dict.fromkeys(slot.evidence_ids)
                          if item_id in known_ids],
        )
        for slot in assessment.answer_slots
        if "".join(slot.question_part.split()) in "".join(query.split())
    ]
    return AgenticRetrievalResult(
        query=query,
        model=DEEPSEEK_MODEL,
        known_facts=plan.known_facts,
        evidence=evidence,
        sufficient=assessment.sufficient,
        assessment=assessment.reason,
        missing_data=_merge_unique([], assessment.missing_data),
        conflicts=assessment.conflicts,
        answer_evidence_ids=[
            item_id for item_id in dict.fromkeys(assessment.answer_evidence_ids)
            if item_id in known_ids
        ][:5],
        answer_slots=answer_slots,
        coverage=coverage,
        validation_issues=validation_issues,
        rounds_completed=max((step.round for step in trace), default=0),
        trace=trace if include_trace else [],
    )
