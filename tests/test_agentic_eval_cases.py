import json
import unittest
from collections import Counter
from pathlib import Path

from tests.agentic_eval import score_case


ROOT = Path(__file__).resolve().parent.parent


class AgenticEvalCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(
            (ROOT / "tests" / "agentic_eval_cases.json").read_text(encoding="utf-8")
        )

    def test_has_balanced_thirty_case_suite(self):
        self.assertEqual(30, len(self.cases))
        self.assertEqual(30, len({case["id"] for case in self.cases}))
        self.assertEqual(
            {
                "多源取证": 6,
                "文档定向检索": 6,
                "确定性计算": 6,
                "证据不足": 6,
                "冲突识别": 6,
            },
            dict(Counter(case["category"] for case in self.cases)),
        )

    def test_expected_sources_exist_in_index(self):
        sources = {
            json.loads(line)["source"]
            for line in (ROOT / "data" / "chunks.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        }
        expected = {
            source
            for case in self.cases
            for source in case["expected_sources"]
        }
        self.assertEqual(set(), expected - sources)

    def test_only_supported_tools_are_required(self):
        supported = {
            "search_knowledge",
            "search_cases",
            "search_document",
            "calculate",
        }
        for case in self.cases:
            self.assertTrue(case["required_tools"], case["id"])
            self.assertLessEqual(set(case["required_tools"]), supported, case["id"])
            self.assertIsInstance(case["expected_sufficient"], bool)

    def test_scoring_tracks_tools_sources_and_sufficiency(self):
        case = self.cases[0]
        result = {
            "baseline": [{"source": case["expected_sources"][0]}],
            "agentic": {
                "sufficient": case["expected_sufficient"],
                "missing_data": [],
                "validation_issues": [],
                "rounds_completed": 1,
                "evidence": [{"source": case["expected_sources"][0]}],
                "trace": [
                    {"task": {"tool": tool}}
                    for tool in case["required_tools"]
                ],
            },
        }

        score = score_case(case, result)

        self.assertEqual(1.0, score["tool_recall"])
        self.assertEqual(1.0, score["tool_precision"])
        self.assertTrue(score["baseline_source_hit"])
        self.assertTrue(score["agentic_source_hit"])
        self.assertTrue(score["sufficiency_correct"])


if __name__ == "__main__":
    unittest.main()
