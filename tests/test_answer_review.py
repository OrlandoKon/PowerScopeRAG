import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pipeline.generate import (
    _format_answer_slots, anchor_answer, review_answer, usable_answer_slots,
)


class AnswerReviewTests(unittest.TestCase):
    def test_anchor_checks_scope_and_uses_reviewed_draft(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="  自洽答案  "))]
        )
        chunks = [{"source": "标准.md", "content": "可使用PyTorch"}]
        with patch("pipeline.generate._get_client", return_value=client):
            answer = anchor_answer("使用什么框架？", chunks, "可使用PyTorch")
        self.assertEqual("自洽答案", answer)
        messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertIn("可使用PyTorch", messages[0]["content"])
        self.assertIn("不能把建议或可能性写成实际发生的事实", messages[0]["content"])
        self.assertIn('"draft": "可使用PyTorch"', messages[1]["content"])

    def test_anchor_failure_preserves_reviewed_answer(self):
        chunks = [{"source": "标准.md", "content": "可使用PyTorch"}]
        client = Mock()
        client.chat.completions.create.side_effect = RuntimeError("timeout")
        with patch("pipeline.generate._get_client", return_value=client):
            self.assertEqual("原答案", anchor_answer("问题", chunks, "原答案"))

    def test_slot_guidance_only_uses_selected_evidence(self):
        chunks = [{"evidence_id": "E2", "source": "标准.md", "content": "378A"}]
        slots = [
            {"question_part": "允许载流量是多少", "evidence_ids": ["E1", "E2"]},
            {"question_part": "最大相电流是多少", "evidence_ids": ["E99"]},
        ]
        formatted = _format_answer_slots(chunks, slots)
        self.assertIn("允许载流量是多少：参考片段 [1]", formatted)
        self.assertIn("最大相电流是多少：当前片段无直接依据", formatted)
        self.assertNotIn("E99", formatted)

    def test_slot_guidance_requires_multiple_supported_parts(self):
        chunks = [{"evidence_id": "E1"}, {"evidence_id": "E2"}]
        complete = [
            {"question_part": "第一项", "evidence_ids": ["E1"]},
            {"question_part": "第二项", "evidence_ids": ["E2"]},
        ]
        self.assertEqual(complete, usable_answer_slots(chunks, complete))
        self.assertEqual([], usable_answer_slots(chunks, complete[:1]))
        self.assertEqual([], usable_answer_slots(chunks, [
            complete[0], {"question_part": "第二项", "evidence_ids": ["E99"]},
        ]))

    def test_review_uses_same_evidence_and_draft(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="  正确答案  "))]
        )
        chunks = [{"source": "标准.md", "content": "允许载流量378A"}]
        with patch("pipeline.generate._get_client", return_value=client):
            answer = review_answer("允许载流量？", chunks, "旧答案")
        self.assertEqual("正确答案", answer)
        request = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertIn("允许载流量378A", request[0]["content"])
        self.assertIn('"draft": "旧答案"', request[1]["content"])

    def test_empty_or_failed_review_preserves_draft(self):
        chunks = [{"source": "标准.md", "content": "允许载流量378A"}]
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=""))]
        )
        with patch("pipeline.generate._get_client", return_value=client):
            self.assertEqual("旧答案", review_answer("问题", chunks, "旧答案"))
        client.chat.completions.create.side_effect = RuntimeError("timeout")
        with patch("pipeline.generate._get_client", return_value=client):
            self.assertEqual("旧答案", review_answer("问题", chunks, "旧答案"))

    def test_no_evidence_does_not_call_model(self):
        with patch("pipeline.generate._get_client") as client:
            self.assertEqual("旧答案", review_answer("问题", [], "旧答案"))
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
