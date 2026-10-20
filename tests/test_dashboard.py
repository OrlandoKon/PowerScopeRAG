import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dashboard import build_dashboard_data


class DashboardDataTests(unittest.TestCase):
    def test_builds_document_case_and_evaluation_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunks_file = root / "chunks.jsonl"
            cases_file = root / "cases.jsonl"
            reports = root / "tests" / "reports"
            reports.mkdir(parents=True)
            chunks_file.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in [
                    {"source": "标准A.md", "doc_type": "regulation"},
                    {"source": "标准A.md", "doc_type": "regulation"},
                    {"source": "论文B.md", "doc_type": "journal"},
                ]),
                encoding="utf-8",
            )
            cases_file.write_text(
                json.dumps({
                    "source": "案例.docx",
                    "verification_status": "待专家确认",
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            report = {
                "retrieval": {"agentic_refined": {
                    "completed": 200, "hit_at_1": 1.0, "hit_at_3": 1.0,
                    "hit_at_5": 1.0, "mrr": 1.0,
                }},
                "ragas": {"agentic_refined": {
                    "completed": 200, "faithfulness": 0.9,
                    "answer_relevancy": 0.8, "context_precision": 0.85,
                    "context_recall": 0.95, "answer_correctness": 0.75,
                }},
            }
            (reports / "agentic_200_comparison_20260930_120000.json").write_text(
                json.dumps(report), encoding="utf-8",
            )

            with patch("dashboard.ROOT", root), patch(
                "dashboard.CHUNKS_FILE", chunks_file,
            ), patch("dashboard.CASES_FILE", cases_file):
                data = build_dashboard_data(3)

        self.assertEqual(2, data["overview"]["documents"])
        self.assertEqual(3, data["overview"]["chunks"])
        self.assertEqual(1, data["overview"]["cases"])
        self.assertEqual(2, data["categories"][0]["chunks"])
        self.assertEqual(200, data["evaluation"]["questions"])
        self.assertEqual(1.0, data["evaluation"]["retrieval"]["mrr"])


if __name__ == "__main__":
    unittest.main()
