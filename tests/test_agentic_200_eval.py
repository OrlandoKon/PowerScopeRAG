import unittest

from tests.agentic_200_eval import (
    _is_insufficient_balance,
    _require_complete_answers,
    _summary,
    select_agentic_evidence,
)


class Agentic200EvalTests(unittest.TestCase):
    def test_round_robin_keeps_multiple_task_types_within_top_five(self):
        result = {
            "evidence": [
                {"evidence_id": f"E{i}", "source": f"S{i}", "chunk_idx": i}
                for i in range(1, 7)
            ],
            "trace": [
                {"evidence_ids": ["E1", "E2", "E3", "E4"]},
                {"evidence_ids": ["E5", "E6"]},
            ],
        }

        selected = select_agentic_evidence(result)

        self.assertEqual(["E1", "E5", "E2", "E6", "E3"], [
            item["evidence_id"] for item in selected
        ])

    def test_retrieval_summary_uses_same_expected_sources(self):
        cases = [{"id": "Q001", "expected_sources": ["right.md"]}]
        cache = {
            "Q001": {
                "ordinary": {"hits": [{"source": "right.md"}]},
                "agentic": {"hits": [{"source": "wrong.md"}]},
            }
        }

        result = _summary(cases, cache)

        self.assertEqual(1.0, result["ordinary"]["hit_at_1"])
        self.assertEqual(0.0, result["agentic"]["hit_at_1"])

    def test_ragas_requires_both_answers_for_every_case(self):
        cases = [{"id": "Q001"}, {"id": "Q002"}]
        cache = {
            "Q001": {"ordinary": {"answer": "a"}, "agentic_assembled": {"answer": "b"}},
            "Q002": {"ordinary": {"answer": "c"}},
        }
        with self.assertRaisesRegex(RuntimeError, "agentic_assembled 仅完成 1/2"):
            _require_complete_answers(cases, cache)

    def test_balance_error_is_detected(self):
        self.assertTrue(_is_insufficient_balance(Exception("Error code: 402 - Insufficient Balance")))
        self.assertFalse(_is_insufficient_balance(Exception("request timed out")))


if __name__ == "__main__":
    unittest.main()
