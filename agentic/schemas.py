"""Structured state exchanged by the Agentic RAG workflow."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


KnowledgeLane = Literal["regulation", "professional", "all", "cases"]
AgentTool = Literal[
    "search_knowledge",
    "search_cases",
    "search_document",
    "calculate",
]


class CalculationSpec(BaseModel):
    operation: Literal["compare", "ratio"]
    left_value: float
    right_value: float
    left_label: str = Field(default="数值A", min_length=1, max_length=80)
    right_label: str = Field(default="数值B", min_length=1, max_length=80)
    unit: str = Field(default="", max_length=20)


class RetrievalTask(BaseModel):
    task_id: str = Field(min_length=1, max_length=40)
    goal: str = Field(min_length=1, max_length=300)
    query: str = Field(min_length=2, max_length=500)
    tool: AgentTool = "search_knowledge"
    lane: KnowledgeLane = "all"
    source: str | None = Field(default=None, max_length=300)
    calculation: CalculationSpec | None = None

    @model_validator(mode="after")
    def validate_tool_arguments(self):
        if self.tool == "search_document" and not self.source:
            raise ValueError("search_document requires source")
        if self.tool == "calculate" and self.calculation is None:
            raise ValueError("calculate requires calculation parameters")
        return self


class RetrievalPlan(BaseModel):
    known_facts: list[str] = Field(default_factory=list, max_length=20)
    missing_data: list[str] = Field(default_factory=list, max_length=20)
    tasks: list[RetrievalTask] = Field(min_length=1, max_length=4)


class Evidence(BaseModel):
    evidence_id: str
    task_id: str
    evidence_type: Literal["knowledge", "case", "calculation"]
    source: str
    content: str
    doc_type: str | None = None
    section: str | None = None
    chunk_idx: int | str | None = None
    case_id: str | None = None
    verification_status: str | None = None
    context_format: str | None = None
    distance: float | None = None
    score: float | None = None
    calculation: dict | None = None


class AnswerSlot(BaseModel):
    question_part: str = Field(min_length=1, max_length=300)
    evidence_ids: list[str] = Field(default_factory=list, max_length=5)


class EvidenceAssessment(BaseModel):
    sufficient: bool
    reason: str = ""
    missing_data: list[str] = Field(default_factory=list, max_length=20)
    conflicts: list[str] = Field(default_factory=list, max_length=20)
    answer_evidence_ids: list[str] = Field(default_factory=list, max_length=5)
    answer_slots: list[AnswerSlot] = Field(default_factory=list, max_length=8)
    follow_up_tasks: list[RetrievalTask] = Field(default_factory=list, max_length=4)


class EvidenceCoverage(BaseModel):
    requirement: str
    description: str
    satisfied: bool
    evidence_ids: list[str] = Field(default_factory=list)


class TraceStep(BaseModel):
    round: int
    task: RetrievalTask
    evidence_ids: list[str]


class AgenticRetrievalResult(BaseModel):
    query: str
    model: str
    known_facts: list[str]
    evidence: list[Evidence]
    sufficient: bool
    assessment: str
    missing_data: list[str]
    conflicts: list[str]
    answer_evidence_ids: list[str] = Field(default_factory=list)
    answer_slots: list[AnswerSlot] = Field(default_factory=list)
    coverage: list[EvidenceCoverage] = Field(default_factory=list)
    validation_issues: list[str] = Field(default_factory=list)
    rounds_completed: int
    trace: list[TraceStep] = Field(default_factory=list)
