import unittest
from unittest.mock import patch

from pipeline.index_text import (
    build_index_text,
    heading_path,
    metadata_match_score,
    source_author,
    source_title,
)
from pipeline.retrieve import (
    _answer_shape_score,
    _fact_alignment_score,
    _hierarchical_select,
    _keyword_match_score,
    _near_duplicate,
    _overlapping_evidence,
    _query_subqueries,
    _source_sparse_candidates,
    _with_structured_context,
    rerank,
)


def hit(
    source: str,
    index: int,
    score: float,
    content: str,
    heading: str,
) -> dict:
    return {
        "source": source,
        "chunk_idx": index,
        "doc_type": "journal",
        "H2": heading,
        "content": content,
        "rerank_score": score,
    }


class IndexTextTests(unittest.TestCase):
    def test_source_title_keeps_searchable_title_and_author(self):
        self.assertEqual(
            "含分布式光伏的低压台区三相线损理论计算及其无功优化 李涵",
            source_title("含分布式光伏的低压台区三相线损理论计算及其无功优化_李涵.md"),
        )
        source = "含分布式光伏的低压台区三相线损理论计算及其无功优化_李涵.md"
        self.assertEqual("李涵", source_author(source))
        self.assertEqual(1.0, metadata_match_score("李涵论文采用什么方法？", {"source": source}))
        self.assertEqual(0.0, metadata_match_score("陈思远论文采用什么方法？", {"source": source}))
        regulation = {"source": "DB1331T084-2024.md", "doc_type": "regulation"}
        self.assertEqual(0.25, metadata_match_score("储能容量应按多少比例配置？", regulation))
        self.assertEqual(
            0.9,
            metadata_match_score("雄安新区电缆通道使用年限要求", regulation),
        )
        self.assertEqual(0.0, metadata_match_score("某论文提出哪些配置要求？", regulation))

    def test_index_text_contains_title_structure_and_original_body(self):
        chunk = {
            "source": "GBT+45418-2025.md",
            "doc_type": "regulation",
            "H1": "7 配电变压器",
            "H2": "7.2 容量要求",
            "content": "单台变压器容量不宜超过规定值。",
        }
        text = build_index_text(chunk)
        self.assertIn("文档标题：GBT 45418-2025", text)
        self.assertIn("文档类型：标准规范", text)
        self.assertIn("章节路径：7 配电变压器 > 7.2 容量要求", text)
        self.assertIn("正文：单台变压器容量不宜超过规定值。", text)
        self.assertEqual("7 配电变压器 > 7.2 容量要求", heading_path(chunk))

    def test_structured_context_is_self_contained_and_preserves_body(self):
        chunk = {
            "source": "某停电分析论文_张三.md",
            "doc_type": "journal",
            "H2": "3.2 原因分析",
            "content": "最大相电流超过电缆允许载流量。",
        }
        result = _with_structured_context([chunk])[0]
        self.assertEqual(chunk["content"], result["body_content"])
        self.assertEqual("structured", result["context_format"])
        self.assertIn("文档标题：某停电分析论文 张三", result["content"])
        self.assertIn("章节路径：3.2 原因分析", result["content"])
        self.assertIn("正文：最大相电流超过电缆允许载流量。", result["content"])

    def test_structured_context_leads_with_matching_original_evidence(self):
        chunk = {
            "source": "分布式光伏研究_张三.md",
            "doc_type": "journal",
            "content": (
                "本文介绍研究背景。"
                "对策包括配电网规划以及无功补偿装置的应用。"
                "最后给出研究展望。"
            ),
        }
        result = _with_structured_context(
            [chunk], "降低线损可以调整哪些因素，包括无功补偿吗？",
        )[0]
        self.assertIn("可直接用于回答问题的原文证据", result["content"])
        self.assertIn("对策包括配电网规划以及无功补偿装置的应用", result["content"])
        self.assertNotIn("本文介绍研究背景", result["content"])

    def test_numeric_question_keeps_all_matching_value_rows(self):
        chunk = {
            "source": "试验数据_张三.md",
            "doc_type": "journal",
            "H2": "表1 试验结果",
            "content": "节点100：20秒，95%。\n节点200：30秒，97%。",
        }
        result = _with_structured_context(
            [chunk], "200个节点的识别时间和准确率分别是多少？",
        )[0]
        self.assertIn("可直接用于回答问题的原文证据", result["content"])
        self.assertIn("节点100：20秒，95%", result["content"])
        self.assertIn("节点200：30秒，97%", result["content"])

    def test_enumerated_clause_keeps_its_following_conditions(self):
        chunk = {
            "source": "重要用户供电标准.md",
            "doc_type": "regulation",
            "H2": "6.2.1 重要电力用户供电电源配置",
            "content": (
                "a)特级重要电力用户应具备三路电源供电条件。"
                "其中两路应来自不同变电站。"
                "电源应追溯至220kV变电站不同母线。\n"
                "b)一级重要电力用户应具备双电源供电条件。"
            ),
        }

        result = _with_structured_context(
            [chunk], "特级重要电力用户应配置几路电源，来源有何要求？",
        )[0]

        self.assertIn("三路电源", result["content"])
        self.assertIn("不同变电站", result["content"])
        self.assertIn("不同母线", result["content"])

    def test_explicit_author_match_overrides_semantic_confuser(self):
        class FakeReranker:
            @staticmethod
            def predict(_pairs):
                return [10.0, 0.0]

        chunks = [
            {
                "source": "同主题研究_李四.md",
                "chunk_idx": 0,
                "doc_type": "journal",
                "content": "语义非常相近的干扰片段。",
            },
            {
                "source": "目标研究_张三.md",
                "chunk_idx": 0,
                "doc_type": "journal",
                "content": "张三研究中的目标答案。",
            },
        ]
        with patch("pipeline.retrieve._get_reranker", return_value=FakeReranker()):
            ranked = rerank("张三研究中采用了什么方法？", chunks, 2, structured=True)
        self.assertEqual("目标研究_张三.md", ranked[0]["source"])

    def test_numeric_fact_lookup_prefers_lexical_table_within_author(self):
        class FakeReranker:
            @staticmethod
            def predict(_pairs):
                return [10.0, 0.0]

        chunks = [
            {
                "source": "目标研究_张三.md",
                "chunk_idx": 1,
                "doc_type": "journal",
                "content": "实验结果总体表现良好。",
                "bm25_score": 0.0,
            },
            {
                "source": "目标研究_张三.md",
                "chunk_idx": "window:2-3",
                "doc_type": "journal",
                "content": "节点数200，识别时间30秒，准确率97%。",
                "bm25_score": 100.0,
            },
        ]
        with patch("pipeline.retrieve._get_reranker", return_value=FakeReranker()):
            ranked = rerank("张三实验中200个节点的时间和准确率分别是多少？", chunks, 2, structured=True)
        self.assertEqual("window:2-3", ranked[0]["chunk_idx"])

    def test_concrete_object_pair_rejects_wrong_scene(self):
        class FakeReranker:
            @staticmethod
            def predict(_pairs):
                return [10.0, 0.0]

        chunks = [
            {
                "source": "综合管廊标准.md",
                "chunk_idx": 1,
                "doc_type": "regulation",
                "content": "综合管廊应急照明照度为5lx，持续供电1h。",
                "bm25_score": 100.0,
            },
            {
                "source": "变电站防涝标准.md",
                "chunk_idx": "window:2-3",
                "doc_type": "regulation",
                "content": "变电站移动式应急照明照度为20lx，持续供电3h。",
                "bm25_score": 60.0,
            },
        ]
        with patch("pipeline.retrieve._get_reranker", return_value=FakeReranker()):
            ranked = rerank(
                "变电站应急照明照度和持续供电时间分别是多少？",
                chunks,
                2,
                structured=True,
            )
        self.assertEqual("变电站防涝标准.md", ranked[0]["source"])

    def test_section_keyword_score_does_not_repeat_title_terms(self):
        chunk = {
            "source": "多源大数据停电场景研究_周鑫.md",
            "doc_type": "journal",
            "H2": "实验参数",
            "content": "本节只讨论遗传算法参数。",
        }
        query = "周鑫研究中的停电场景包含哪些属性？"
        document_score = _keyword_match_score(query, chunk)
        section_score = _keyword_match_score(query, chunk, section_only=True)
        self.assertGreater(document_score, section_score)

    def test_enumeration_question_prefers_explicit_list_evidence(self):
        query = "停电场景包含哪些维度，共多少个属性？"
        list_chunk = {
            "content": "常见故障类型包括台区无电、主线无电,以及支线无电。",
        }
        summary_chunk = {
            "content": "本文采用多源大数据开展配电网停电故障分析。",
        }
        self.assertEqual(1.0, _answer_shape_score(query, list_chunk))
        self.assertEqual(0.0, _answer_shape_score(query, summary_chunk))

    def test_numeric_table_has_expected_answer_shape(self):
        chunk = {
            "content": "|相导体电压(V)|时间(s)|\n|---|---|\n|220|0.4|\n|380|0.2|",
        }
        self.assertEqual(
            1.0,
            _answer_shape_score("220V和380V切断时间分别是多少？", chunk),
        )

    def test_relation_question_prefers_direct_equivalence_statement(self):
        query = "计算节点功率时，将分布式光伏等效为什么？"
        direct = {"content": "分布式光伏接入后，可将其视作负的负荷。"}
        background = {"content": "本文分析分布式光伏接入对节点电压的影响。"}
        self.assertEqual(1.0, _answer_shape_score(query, direct))
        self.assertEqual(0.0, _answer_shape_score(query, background))

    def test_multi_part_question_is_split_for_section_retrieval(self):
        query = (
            "修正等值电阻法如何处理接入电流？"
            "计算负荷电流向量时，将分布式光伏等效为什么？"
        )
        self.assertEqual(
            [
                "修正等值电阻法如何处理接入电流",
                "计算负荷电流向量时，将分布式光伏等效为什么",
            ],
            _query_subqueries(query),
        )

    def test_fact_alignment_prefers_the_table_with_the_requested_measure(self):
        query = "供电区域分为哪几类，A+类供电区域的负荷密度是多少？"
        correct = {
            "H2": "表1 供电区域划分表",
            "content": (
                "|供电区域类型|A+|A|B|C|D|E|\n"
                "|---|---|---|---|---|---|---|\n"
                "|参考饱和负荷密度 MW/km2|σ≥30|15≤σ<30|6≤σ<15|1≤σ<6|0.1≤σ<1|σ<0.1|"
            ),
        }
        wrong = {
            "H2": "表3 短路电流限定值",
            "content": (
                "|A+类、A类、B类供电区域|C类供电区域|D类、E类供电区域|\n"
                "|---|---|---|\n|20|16|16|\n\n"
                "目标网架宜依据供电区域类型和负荷密度选择。"
            ),
        }
        self.assertGreater(
            _fact_alignment_score(query, correct),
            _fact_alignment_score(query, wrong),
        )


class HierarchicalSelectionTests(unittest.TestCase):
    def test_near_duplicate_ignores_spacing_and_minor_punctuation(self):
        left = "低压电缆持续过负荷，导致电缆本体和接头严重发热。"
        right = "低压电缆持续过负荷导致电缆本体和接头严重发热"
        self.assertTrue(_near_duplicate(left, right))
        self.assertTrue(_near_duplicate(left, left + "最大相电流为492.5A。"))

    def test_overlapping_parent_windows_are_duplicate_evidence(self):
        left = hit("paper.md", 1, 0.9, "第一段。", "方法")
        left["chunk_idx"] = "window:1-2"
        left["window_chunk_indices"] = [1, 2]
        right = hit("paper.md", 2, 0.8, "第二段。", "方法")
        right["chunk_idx"] = "window:2-3"
        right["window_chunk_indices"] = [2, 3]
        other = hit("other.md", 2, 0.8, "第二段。", "方法")
        self.assertTrue(_overlapping_evidence(left, right))
        self.assertFalse(_overlapping_evidence(left, other))

    def test_source_candidates_include_adjacent_windows(self):
        class FakeBm25:
            @staticmethod
            def get_scores(_tokens):
                return [2.0, 1.0, 0.1]

        chunks = [
            hit("paper.md", 0, 0.0, "台区无电、主线无", "故障类型"),
            hit("paper.md", 1, 0.0, "电，以及支线无电。", "故障类型"),
            hit("other.md", 0, 0.0, "其他内容。", "其他"),
        ]
        for chunk in chunks:
            chunk["body_content"] = chunk["content"]
        with patch("pipeline.retrieve._get_bm25", return_value=(FakeBm25(), chunks)):
            candidates = _source_sparse_candidates("停电类型", ["paper.md"], per_source=1)
        windows = [row for row in candidates if str(row["chunk_idx"]).startswith("window:")]
        self.assertEqual(1, len(windows))
        self.assertIn("台区无电、主线无", windows[0]["content"])
        self.assertIn("电，以及支线无电", windows[0]["content"])
        self.assertIn("主线无电", windows[0]["body_content"])
        self.assertIn("支线无电", windows[0]["body_content"])

    def test_high_confidence_document_can_supply_four_chunks(self):
        chunks = [
            hit("correct.md", 0, 0.99, "原因分析：电缆过载发热。", "原因分析"),
            hit("correct.md", 1, 0.94, "运行数据：最大相电流492.5A。", "运行情况"),
            hit("correct.md", 2, 0.90, "设计依据：允许载流量378A。", "判断依据"),
            hit("correct.md", 3, 0.86, "防范措施：检测温升并改造电缆。", "防范措施"),
            hit("noise-a.md", 0, 0.52, "配变重过载的一般治理方法。", "治理"),
            hit("noise-b.md", 0, 0.40, "低电压问题的一般分析。", "分析"),
        ]
        selected = _hierarchical_select(chunks, top_k=5)
        self.assertEqual(4, sum(row["source"] == "correct.md" for row in selected))
        self.assertEqual("correct.md", selected[0]["source"])

    def test_explicit_document_constraint_can_fill_top_five(self):
        chunks = [
            hit("author.md", index, 0.99 - index * 0.05, f"互补证据{index}：不同事实。", f"章节{index}")
            for index in range(5)
        ]
        selected = _hierarchical_select(chunks, top_k=5, primary_max=5)
        self.assertEqual(5, len(selected))
        self.assertEqual({"author.md"}, {row["source"] for row in selected})

    def test_low_confidence_results_keep_source_diversity(self):
        chunks = [
            hit("a.md", 0, 0.90, "甲文档第一条不同证据。", "一"),
            hit("a.md", 1, 0.70, "甲文档第二条不同证据。", "二"),
            hit("a.md", 2, 0.50, "甲文档第三条不同证据。", "三"),
            hit("b.md", 0, 0.895, "乙文档第一条不同证据。", "一"),
            hit("b.md", 1, 0.69, "乙文档第二条不同证据。", "二"),
            hit("c.md", 0, 0.89, "丙文档第一条不同证据。", "一"),
        ]
        selected = _hierarchical_select(chunks, top_k=5)
        self.assertLessEqual(
            max(sum(row["source"] == source for row in selected) for source in {"a.md", "b.md", "c.md"}),
            2,
        )
        self.assertEqual(3, len({row["source"] for row in selected}))

    def test_near_duplicate_chunks_do_not_fill_top_k(self):
        chunks = [
            hit("a.md", 0, 0.99, "低压电缆持续过负荷导致电缆接头严重发热。", "原因"),
            hit("a.md", 1, 0.98, "低压电缆持续过负荷，导致电缆接头严重发热。", "原因"),
            hit("a.md", 2, 0.90, "最大相电流为492.5A。", "数据"),
            hit("b.md", 0, 0.60, "允许持续载流量为378A。", "参数"),
        ]
        selected = _hierarchical_select(chunks, top_k=4)
        self.assertEqual(3, len(selected))
        self.assertEqual(1, sum("持续过负荷" in row["content"] for row in selected))


if __name__ == "__main__":
    unittest.main()
