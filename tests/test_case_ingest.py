import json
import tempfile
import unittest
from pathlib import Path

from config import CASE_SOURCE_FILE
from pipeline.case_ingest import build_cases, export_verified_eval_cases


class CaseIngestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = build_cases(CASE_SOURCE_FILE)

    def test_extracts_all_leaf_cases(self):
        self.assertEqual(34, len(self.cases))
        self.assertEqual(34, len({case["case_id"] for case in self.cases}))
        self.assertEqual(34, len({case["cause_code"] for case in self.cases}))

    def test_overload_case_is_structured(self):
        case = next(c for c in self.cases if c["cause_code"] == "2.1.1")
        self.assertEqual("CASE-2.1.1-001", case["case_id"])
        self.assertIn("持续38°C高温", case["trigger"])
        self.assertIn("台区配变负荷超额定132%", case["observations"])
        self.assertEqual("全小区420户停电", case["outage_scope"])
        self.assertEqual("错峰分流负荷后晚间复电", case["action"])
        self.assertNotIn(case["cause_name"], case["diagnostic_input"])

    def test_unverified_cases_are_not_exported_for_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "case_eval.json"
            count = export_verified_eval_cases(self.cases, output)
            self.assertEqual(0, count)
            self.assertEqual([], json.loads(output.read_text(encoding="utf-8")))

    def test_action_words_inside_event_do_not_shift_fields(self):
        photovoltaic = next(c for c in self.cases if c["cause_code"] == "2.4.1")
        self.assertIn("逆向漏电电流", " ".join(photovoltaic["observations"]))
        self.assertEqual("整改逆变器接地工艺后消除逆流误跳", photovoltaic["action"])

        excavation = next(c for c in self.cases if c["cause_code"] == "3.2.1")
        self.assertIn("挖掘机误挖深埋低压电缆", excavation["observations"])
        self.assertEqual("周边5个居民小区瞬时停电", excavation["outage_scope"])

    def test_verified_export_keeps_label_out_of_input(self):
        reviewed = [dict(self.cases[0])]
        reviewed[0]["verification_status"] = "已确认"
        reviewed[0]["evaluation_eligible"] = True
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "case_eval.json"
            count = export_verified_eval_cases(reviewed, output)
            row = json.loads(output.read_text(encoding="utf-8"))[0]
            self.assertEqual(1, count)
            self.assertNotIn(reviewed[0]["cause_name"], row["user_input"])
            self.assertEqual(reviewed[0]["cause_code"], row["reference"]["cause_code"])
            self.assertEqual([reviewed[0]["case_id"]], row["exclude_case_ids"])


if __name__ == "__main__":
    unittest.main()
