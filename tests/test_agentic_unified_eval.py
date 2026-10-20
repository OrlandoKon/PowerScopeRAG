import unittest
from unittest.mock import MagicMock, patch

from tests.agentic_unified_eval import (
    _answer_fingerprint,
    _generate,
    _reassemble,
    _retrieve,
    _summary,
)


class UnifiedEvalTests(unittest.TestCase):
    def test_retrieval_refresh_keeps_answer_only_for_unchanged_context(self):
        case = {"id": "Q1", "question": "电缆载流量是多少？"}
        original = [{"source": "标准.md", "chunk_idx": 1, "content": "378A"}]
        response = MagicMock()
        response.json.return_value = {"evidence": [], "trace": []}
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.return_value = response
        metadata_only = [{**original[0], "evidence_id": "E17"}]
        for hits, expected_answer in ((original, "378A"),
                                      (metadata_only, "378A"), (
            [{"source": "标准.md", "chunk_idx": 2, "content": "400A"}], None,
        )):
            cache = {"Q1": {
                "fingerprint": "old", "question": case["question"],
                "hits": original, "answer": "378A",
            }}
            with patch("tests.agentic_unified_eval.httpx.Client", return_value=client), patch(
                "tests.agentic_unified_eval.assemble_planned_answer_context",
                return_value=hits,
            ), patch(
                "tests.agentic_unified_eval.refine_answer_context", return_value=hits,
            ), patch("tests.agentic_unified_eval._save"):
                _retrieve([case], cache, "http://test", "new", refresh=True)
            self.assertEqual(expected_answer, cache["Q1"].get("answer"))

    def test_reassemble_reuses_trace_and_invalidates_old_answer(self):
        case = {"id": "Q1", "question": "允许载流量是多少？"}
        cache = {"Q1": {
            "fingerprint": "old", "question": case["question"], "answer": "旧答案",
            "result": {
                "evidence": [{"evidence_id": "E1", "source": "标准.md",
                              "chunk_idx": 1, "content": "378A"}],
                "trace": [{"task": {"tool": "search_knowledge", "lane": "all"},
                           "evidence_ids": ["E1"]}],
            },
        }}
        with patch("tests.agentic_unified_eval._save") as save, patch(
            "agentic.assembly._original_chunks", return_value={},
        ):
            _reassemble([case], cache, "new")

        self.assertEqual("new", cache["Q1"]["fingerprint"])
        self.assertEqual("378A", cache["Q1"]["hits"][0]["content"])
        self.assertNotIn("answer", cache["Q1"])
        save.assert_called_once()

    def test_summary_ignores_stale_rows_and_reports_misses(self):
        cases = [
            {"id": "Q1", "expected_sources": ["right.md"]},
            {"id": "Q2", "expected_sources": ["right.md"]},
            {"id": "Q3", "expected_sources": ["right.md"]},
        ]
        cache = {
            "Q1": {"fingerprint": "current", "hits": [{"source": "right.md"}]},
            "Q2": {"fingerprint": "current", "hits": [{"source": "wrong.md"}]},
            "Q3": {"fingerprint": "old", "hits": [{"source": "right.md"}]},
        }

        summary = _summary(cases, cache, "current")

        self.assertEqual(2, summary["completed"])
        self.assertEqual(3, summary["total"])
        self.assertEqual(0.5, summary["hit_at_1"])
        self.assertEqual(["Q2"], summary["misses"])

    def test_reassemble_keeps_answer_when_context_unchanged(self):
        case = {"id": "Q1", "question": "允许载流量是多少？"}
        evidence = {"evidence_id": "E1", "source": "标准.md",
                    "chunk_idx": 1, "content": "378A"}
        cache = {"Q1": {
            "fingerprint": "old", "question": case["question"],
            "answer": "允许载流量为378A。", "hits": [evidence],
            "result": {
                "evidence": [evidence],
                "trace": [{"task": {"tool": "search_knowledge", "lane": "all"},
                           "evidence_ids": ["E1"]}],
            },
        }}
        with patch("tests.agentic_unified_eval._save"), patch(
            "agentic.assembly._original_chunks", return_value={},
        ):
            _reassemble([case], cache, "new")

        self.assertEqual("允许载流量为378A。", cache["Q1"]["answer"])

    def test_generation_replaces_stale_answer_and_reuses_current_answer(self):
        case = {"id": "Q1", "question": "允许载流量是多少？"}
        hits = [{"source": "标准.md", "content": "允许载流量378A"}]
        cache = {"Q1": {
            "fingerprint": "current", "hits": hits, "answer": "旧答案",
        }}
        with patch("pipeline.generate.rag_answer", return_value="草稿") as generate, patch(
            "pipeline.generate.review_answer", return_value="允许载流量378A"
        ) as review, patch("tests.agentic_unified_eval._save"):
            _generate([case], cache, "current")
            _generate([case], cache, "current")
        self.assertEqual("允许载流量378A", cache["Q1"]["answer"])
        self.assertEqual(
            _answer_fingerprint(case["question"], hits),
            cache["Q1"]["answer_fingerprint"],
        )
        generate.assert_called_once()
        review.assert_called_once_with(case["question"], hits, "草稿")


if __name__ == "__main__":
    unittest.main()
