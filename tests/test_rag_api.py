import unittest
from unittest.mock import patch

from agentic.schemas import (
    AgenticRetrievalResult,
    AnswerSlot,
    Evidence,
    RetrievalTask,
    TraceStep,
)
from api import RagRequest, _generate_agentic_answer, app, rag


def _result() -> AgenticRetrievalResult:
    return AgenticRetrievalResult(
        query="测试问题",
        model="test-model",
        known_facts=[],
        evidence=[],
        sufficient=False,
        assessment="需要补充数据",
        missing_data=["现场量测"],
        conflicts=[],
        rounds_completed=1,
        trace=[],
    )


class RagApiTests(unittest.TestCase):
    def test_slot_guidance_is_opt_in(self):
        result = _result().model_copy(update={"answer_slots": [
            AnswerSlot(question_part="第一项", evidence_ids=["E1"]),
            AnswerSlot(question_part="第二项", evidence_ids=["E2"]),
        ]})
        hits = [
            {"evidence_id": "E1", "source": "标准.md", "content": "第一项"},
            {"evidence_id": "E2", "source": "标准.md", "content": "第二项"},
        ]
        with patch("api.assemble_planned_answer_context", return_value=hits), patch(
            "api.refine_answer_context", return_value=hits,
        ), patch("api.rag_answer", return_value="答案") as generation, patch(
            "api.review_answer", return_value="答案",
        ), patch("api.ENABLE_ANSWER_SLOT_GUIDANCE", False), patch(
            "api.ENABLE_ANSWER_ANCHOR", False,
        ):
            _generate_agentic_answer("第一项和第二项", result, 5)
        self.assertEqual([], generation.call_args.kwargs["answer_slots"])

        with patch("api.assemble_planned_answer_context", return_value=hits), patch(
            "api.refine_answer_context", return_value=hits,
        ), patch("api.rag_answer", return_value="答案") as generation, patch(
            "api.review_answer", return_value="答案",
        ), patch("api.ENABLE_ANSWER_SLOT_GUIDANCE", True), patch(
            "api.ENABLE_ANSWER_ANCHOR", False,
        ):
            _generate_agentic_answer("第一项和第二项", result, 5)
        self.assertEqual(2, len(generation.call_args.kwargs["answer_slots"]))

    def test_answer_generation_uses_only_planned_evidence(self):
        result = _result().model_copy(update={
            "evidence": [Evidence(
                evidence_id="E1", task_id="T1", evidence_type="knowledge",
                source="标准.md", content="允许载流量378A", doc_type="regulation",
                chunk_idx=1,
            )],
            "trace": [TraceStep(
                round=1,
                task=RetrievalTask(
                    task_id="T1", goal="查找标准", query="电缆允许载流量",
                ),
                evidence_ids=["E1"],
            )],
        })
        with patch("api.retrieve_chunks") as ordinary_search, patch(
            "agentic.assembly._original_chunks", return_value={},
        ), patch("api.rag_answer", return_value="378A") as generation, patch(
            "api.review_answer", return_value="允许载流量378A"
        ) as review, patch("api.anchor_answer", return_value="电缆允许载流量378A") as anchor:
            answer, sources = _generate_agentic_answer("允许载流量是多少？", result, 5)

        self.assertEqual("电缆允许载流量378A", answer)
        self.assertEqual(["标准.md"], [item.source for item in sources])
        self.assertEqual(1, len(generation.call_args.args[1]))
        review.assert_called_once()
        self.assertEqual("378A", review.call_args.args[2])
        anchor.assert_called_once()
        self.assertEqual("允许载流量378A", anchor.call_args.args[2])
        ordinary_search.assert_not_called()

    def test_public_schema_has_one_business_endpoint(self):
        operations = {
            path for path, methods in app.openapi()["paths"].items()
            if "post" in methods
        }
        self.assertEqual({"/rag"}, operations)

    def test_evidence_mode_does_not_generate_answer(self):
        with patch("api._main_collection_count", 1), patch(
            "api.run_agentic_retrieval", return_value=_result()
        ) as retrieval, patch("api._generate_agentic_answer") as generation:
            response = rag(RagRequest(query="测试问题", include_trace=False))

        self.assertFalse(response.sufficient)
        self.assertEqual(["现场量测"], response.missing_data)
        self.assertIsNone(response.answer)
        self.assertEqual([], response.sources)
        self.assertFalse(retrieval.call_args.kwargs["include_trace"])
        generation.assert_not_called()

    def test_answer_mode_uses_same_evidence_result(self):
        result = _result()
        with patch("api._main_collection_count", 1), patch(
            "api.run_agentic_retrieval", return_value=result
        ) as retrieval, patch(
            "api._generate_agentic_answer", return_value=("回答", [])
        ) as generation:
            response = rag(RagRequest(
                query="测试问题", generate_answer=True, include_trace=False
            ))

        self.assertEqual("回答", response.answer)
        self.assertEqual([], response.trace)
        self.assertTrue(retrieval.call_args.kwargs["include_trace"])
        generation.assert_called_once_with("测试问题", result, 5)


if __name__ == "__main__":
    unittest.main()
