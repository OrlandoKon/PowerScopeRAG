import unittest
from unittest.mock import patch

from agentic.assembly import (
    _section_coverage,
    assemble_answer_context,
    assemble_planned_answer_context,
    refine_answer_context,
)


class AgenticAssemblyTests(unittest.TestCase):
    def test_missing_section_has_no_coverage(self):
        self.assertEqual(0.0, _section_coverage("供电电源要求", None))

    def test_planned_context_anchors_first_knowledge_task(self):
        result = {
            "evidence": [
                {"evidence_id": "E1", "source": "标准.md", "chunk_idx": 1,
                 "content": "电缆载流量378A"},
                {"evidence_id": "E2", "source": "标准.md", "chunk_idx": 2,
                 "content": "应检查最大相电流"},
                {"evidence_id": "E3", "source": "案例.docx", "chunk_idx": 1,
                 "content": "最大相电流492.5A，过载停电"},
            ],
            "trace": [
                {"task": {"tool": "search_knowledge", "lane": "regulation"},
                 "evidence_ids": ["E1", "E2"]},
                {"task": {"tool": "search_cases", "lane": "cases"},
                 "evidence_ids": ["E3"]},
            ],
        }

        selected = assemble_planned_answer_context(
            "最大相电流和电缆允许载流量是多少？有无案例？", result,
        )

        self.assertEqual({"标准.md", "案例.docx"},
                         {item["source"] for item in selected})
        self.assertIn("E1", {item["evidence_id"] for item in selected})
        self.assertIn("E2", {item["evidence_id"] for item in selected})

    def test_assessor_promotes_direct_evidence_from_trusted_source(self):
        result = {
            "evidence": [
                {"evidence_id": f"E{i}", "source": "标准.md",
                 "chunk_idx": i, "content": f"片段{i}"}
                for i in range(1, 7)
            ],
            "trace": [
                {"task": {"tool": "search_knowledge", "lane": "regulation"},
                 "evidence_ids": [f"E{i}" for i in range(1, 6)]},
                {"task": {"tool": "search_knowledge", "lane": "regulation"},
                 "evidence_ids": ["E6"]},
            ],
            "answer_evidence_ids": ["E6"],
        }

        selected = assemble_planned_answer_context("供电电源要求", result)

        self.assertEqual("E6", selected[0]["evidence_id"])
        self.assertEqual(5, len(selected))

    def test_named_author_blocks_other_paper_from_leading(self):
        correct = "含光伏台区线损计算_李涵.md"
        wrong = "含光伏台区线损异常_陈思远.md"
        result = {
            "evidence": [
                {"evidence_id": "E1", "source": correct, "chunk_idx": 1,
                 "content": "修正等值电阻法"},
                {"evidence_id": "E2", "source": wrong, "chunk_idx": 2,
                 "content": "修正等值电阻法处理分布式光伏电流"},
                {"evidence_id": "E3", "source": correct, "chunk_idx": 3,
                 "content": "光伏等效为负的负荷电流"},
            ],
            "trace": [
                {"task": {"tool": "search_knowledge", "lane": "professional"},
                 "evidence_ids": ["E1", "E2"]},
                {"task": {"tool": "search_document", "source": correct},
                 "evidence_ids": ["E3"]},
            ],
            "answer_evidence_ids": ["E2", "E3"],
        }

        selected = assemble_planned_answer_context(
            "李涵论文中如何处理分布式光伏电流？", result,
        )

        self.assertEqual(correct, selected[0]["source"])
        self.assertEqual("E3", selected[0]["evidence_id"])

    def test_planned_context_supports_case_only_task(self):
        result = {
            "evidence": [
                {"evidence_id": "E1", "source": "案例.docx", "chunk_idx": 1,
                 "content": "过载停电案例"},
            ],
            "trace": [
                {"task": {"tool": "search_cases", "lane": "cases"},
                 "evidence_ids": ["E1"]},
            ],
        }

        selected = assemble_planned_answer_context("查找过载停电案例", result)

        self.assertEqual(["E1"], [item["evidence_id"] for item in selected])

    def test_planned_follow_up_can_add_another_source_in_same_lane(self):
        result = {
            "evidence": [
                {"evidence_id": "E1", "source": "标准A.md", "chunk_idx": 1,
                 "content": "电缆保护要求"},
                {"evidence_id": "E2", "source": "标准B.md", "chunk_idx": 2,
                 "content": "电缆保护动作整定值"},
            ],
            "trace": [
                {"task": {"tool": "search_knowledge", "lane": "regulation"},
                 "evidence_ids": ["E1"]},
                {"task": {"tool": "search_knowledge", "lane": "regulation"},
                 "evidence_ids": ["E2"]},
            ],
        }

        selected = assemble_planned_answer_context("电缆保护动作整定值", result)

        self.assertEqual({"标准A.md", "标准B.md"},
                         {item["source"] for item in selected})

    def test_planned_case_context_discloses_verification_status(self):
        result = {
            "evidence": [{
                "evidence_id": "E1", "evidence_type": "case",
                "source": "案例库.docx", "case_id": "CASE-1",
                "verification_status": "待专家确认", "chunk_idx": 1,
                "content": "过载保护动作后停电",
            }],
            "trace": [{
                "task": {"tool": "search_cases", "lane": "cases"},
                "evidence_ids": ["E1"],
            }],
        }

        selected = assemble_planned_answer_context("查找相似案例", result)

        self.assertIn("待专家确认", selected[0]["content"])
        self.assertIn("不能直接认定为当前事件事实", selected[0]["content"])
        self.assertEqual("过载保护动作后停电", result["evidence"][0]["content"])

    def test_scoped_title_match_promotes_recovered_source(self):
        query = "基于Transformer模型的低压停电补全方法提取哪些指标？"
        result = {
            "evidence": [
                {"evidence_id": "E1", "source": "配电网故障定位与供电恢复方法研究_郭上华.md",
                 "chunk_idx": 1, "content": "低压停电补全方法及指标"},
                {"evidence_id": "E2", "source": "基于Transformer的低压停电补全分析_王大鹏.md",
                 "chunk_idx": 2, "content": "Transformer模型提取故障指标"},
            ],
            "trace": [
                {"task": {"tool": "search_knowledge", "lane": "professional"},
                 "evidence_ids": ["E1"]},
                {"task": {"tool": "search_document", "lane": "all",
                          "source": "基于Transformer的低压停电补全分析_王大鹏.md"},
                 "evidence_ids": ["E2"]},
            ],
        }

        selected = assemble_planned_answer_context(query, result)

        self.assertEqual("E2", selected[0]["evidence_id"])

    def test_scoped_answer_section_replaces_low_value_context(self):
        query = "雄安新区特级电力用户应配置几路电源，对电源来源有何要求？"
        primary = [
            {"evidence_id": f"E{i}", "source": "标准.md", "chunk_idx": i,
             "section": "条文说明", "content": "雄安新区特级电力用户的电源要求"}
            for i in range(1, 6)
        ]
        scoped = {"evidence_id": "E6", "source": "标准.md", "chunk_idx": 20,
                  "section": "6.2.1 重要电力用户供电电源配置",
                  "content": "相邻一级用户的供电要求", "context_format": "structured"}
        result = {
            "evidence": [*primary, scoped],
            "trace": [
                {"task": {"tool": "search_knowledge", "lane": "regulation"},
                 "evidence_ids": [item["evidence_id"] for item in primary]},
                {"task": {"tool": "search_document", "source": "标准.md"},
                 "evidence_ids": ["E6"]},
            ],
        }
        chunks = {("标准.md", 20): {
            "content": "a)特级用户应有三路电源，其中两路来自不同变电站。",
        }}
        with patch("agentic.assembly._original_chunks", return_value=chunks):
            selected = assemble_planned_answer_context(query, result)

        self.assertEqual(5, len(selected))
        self.assertIn("E1", {item["evidence_id"] for item in selected})
        self.assertIn("E2", {item["evidence_id"] for item in selected})
        self.assertEqual("E6", selected[0]["evidence_id"])
        self.assertIn("三路电源", next(
            item["content"] for item in selected if item["evidence_id"] == "E6"
        ))

    def test_preserves_top_two_ordinary_evidence(self):
        ordinary = [
            {"source": f"ordinary-{i}", "chunk_idx": i, "content": "无关文本"}
            for i in range(5)
        ]
        agentic = [
            {"source": f"agentic-{i}", "chunk_idx": i, "content": "电缆隧道逃生口吊装口井盖"}
            for i in range(5)
        ]

        selected = assemble_answer_context(
            "电缆隧道逃生口吊装口井盖要求", ordinary, agentic,
        )

        self.assertEqual(5, len(selected))
        self.assertIn(ordinary[0], selected)
        self.assertIn(ordinary[1], selected)

    def test_more_complete_evidence_can_rank_first(self):
        ordinary = [
            {"source": "泛化标准.md", "chunk_idx": 1, "content": "电缆支架间距"},
            {"source": "目标标准.md", "chunk_idx": 2, "content": "逃生口间距"},
        ]
        agentic = [
            {"source": "目标标准.md", "chunk_idx": 3,
             "content": "电缆隧道逃生口间距、吊装口间距及井盖直径"},
        ]

        selected = assemble_answer_context(
            "电缆隧道的逃生口间距和吊装口间距是多少？井盖直径是多少？",
            ordinary,
            agentic,
        )

        self.assertEqual("目标标准.md", selected[0]["source"])

    def test_ignores_untrusted_source_for_single_document_question(self):
        ordinary = [
            {"source": "目标论文.md", "chunk_idx": 1, "content": "年利润和网损"},
            {"source": "目标论文.md", "chunk_idx": 2, "content": "单层双层规划"},
        ]
        agentic = [
            {"source": "其他论文.md", "chunk_idx": 3,
             "content": "年利润网损双层规划单层规划改善"},
        ]

        selected = assemble_answer_context(
            "单层和双层规划的年利润与网损改善是多少？", ordinary, agentic,
        )

        self.assertEqual("目标论文.md", selected[0]["source"])
        self.assertNotIn("其他论文.md", {item["source"] for item in selected})

    def test_refinement_restores_answer_text_from_selected_window(self):
        chunks = {
            ("标准.md", 8): {"content": "监测点类别说明。"},
            ("标准.md", 9): {"content": "D类每百台配电变压器至少设2个监测点。"},
        }
        hits = [{"source": "标准.md", "chunk_idx": "window:8-9",
                 "content": "D类监测点。", "context_format": "structured"}]
        with patch("agentic.assembly._original_chunks", return_value=chunks), patch(
            "pipeline.retrieve._source_sparse_candidates",
            return_value=[{"source": "标准.md", "chunk_idx": 9,
                           "content": chunks[("标准.md", 9)]["content"], "bm25_score": 8.0}],
        ):
            refined = refine_answer_context("D类监测点的密度是多少？", hits)
        self.assertIn("每百台配电变压器至少设2个", refined[0]["content"])
        self.assertEqual(9, refined[0]["chunk_idx"])
        self.assertNotIn("context_format", refined[0])

    def test_refinement_recovers_stronger_section_from_same_source(self):
        chunks = {
            ("标准.md", 1): {"content": "重要用户章节目录。"},
            ("标准.md", 2): {"content": "重要用户分类。"},
            ("标准.md", 20): {"content": "特级重要用户应有三路电源。"},
        }
        hits = [
            {"source": "标准.md", "chunk_idx": 1, "content": "章节目录"},
            {"source": "标准.md", "chunk_idx": 2, "content": "用户分类"},
        ]
        candidates = [
            {"source": "标准.md", "chunk_idx": 20,
             "content": chunks[("标准.md", 20)]["content"], "bm25_score": 12.0},
            {"source": "标准.md", "chunk_idx": 1,
             "content": chunks[("标准.md", 1)]["content"], "bm25_score": 10.0},
        ]
        with patch("agentic.assembly._original_chunks", return_value=chunks), patch(
            "pipeline.retrieve._source_sparse_candidates", return_value=candidates,
        ):
            refined = refine_answer_context("特级用户应有几路电源？", hits)
        self.assertEqual(2, len(refined))
        self.assertEqual(20, refined[1]["chunk_idx"])
        self.assertIn("三路电源", refined[1]["content"])

    def test_refinement_keeps_non_lead_evidence_compact(self):
        chunks = {
            ("标准.md", 1): {"content": "第一段完整原文。"},
            ("标准.md", 2): {"content": "第二段很长的完整原文。"},
        }
        hits = [
            {"source": "标准.md", "chunk_idx": 1, "content": "第一段摘要"},
            {"source": "标准.md", "chunk_idx": 2, "content": "第二段摘要",
             "context_format": "structured"},
        ]
        candidates = [
            {"source": "标准.md", "chunk_idx": 1,
             "content": chunks[("标准.md", 1)]["content"], "bm25_score": 8.0},
            {"source": "标准.md", "chunk_idx": 2,
             "content": chunks[("标准.md", 2)]["content"], "bm25_score": 7.0},
        ]
        with patch("agentic.assembly._original_chunks", return_value=chunks), patch(
            "pipeline.retrieve._source_sparse_candidates", return_value=candidates,
        ):
            refined = refine_answer_context("第一段是什么？", hits)

        self.assertEqual("第一段完整原文。", refined[0]["content"])
        self.assertEqual("第二段摘要", refined[1]["content"])
        self.assertEqual("structured", refined[1]["context_format"])

    def test_refinement_without_search_restores_only_selected_chunk(self):
        chunks = {("标准.md", 1): {"content": "完整条款，允许载流量378A。"}}
        hits = [{"source": "标准.md", "chunk_idx": 1,
                 "content": "精炼片段"}]
        with patch("agentic.assembly._original_chunks", return_value=chunks), patch(
            "pipeline.retrieve._source_sparse_candidates"
        ) as sparse_search:
            refined = refine_answer_context(
                "允许载流量是多少？", hits, allow_search=False,
            )

        self.assertEqual("完整条款，允许载流量378A。", refined[0]["content"])
        sparse_search.assert_not_called()

    def test_refinement_restores_one_complementary_window(self):
        chunks = {
            ("标准.md", 1): {"content": "三层网络架构。"},
            ("标准.md", 2): {"content": "终端感知层实现数据采集和监测。"},
        }
        hits = [
            {"source": "标准.md", "chunk_idx": 1, "content": "三层网络架构。"},
            {"source": "标准.md", "chunk_idx": 2, "content": "终端感知层。"},
        ]
        with patch("agentic.assembly._original_chunks", return_value=chunks), patch(
            "pipeline.retrieve._source_sparse_candidates"
        ) as sparse_search:
            refined = refine_answer_context(
                "终端感知层如何实现数据采集和监测？", hits,
                allow_search=False,
            )

        self.assertIn("实现数据采集和监测", refined[1]["content"])
        sparse_search.assert_not_called()
