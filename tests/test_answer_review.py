import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pipeline.generate import review_answer


class AnswerReviewTests(unittest.TestCase):
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
