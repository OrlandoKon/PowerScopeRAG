import unittest

from pipeline.clean import (
    _REF_SECTION_RE,
    clean_common,
    clean_journal,
    clean_regulation,
)


class JournalCleaningTests(unittest.TestCase):
    def test_markdown_reference_heading_is_detected(self):
        self.assertIsNotNone(_REF_SECTION_RE.match("## 参考文献"))
        self.assertIsNotNone(_REF_SECTION_RE.match("**参 考 文 献**"))

    def test_mid_document_author_bio_does_not_truncate_later_sections(self):
        text = """## 1 研究背景
第一页技术正文。

作者简介：张三，研究方向为配电网。
收稿日期：2026-01-01

## 2 关键方法
第二页关键技术正文。

## 参考文献
- [1] 无关引用。
"""
        cleaned = clean_journal(text)
        self.assertIn("第二页关键技术正文", cleaned)
        self.assertNotIn("作者简介", cleaned)
        self.assertNotIn("无关引用", cleaned)

    def test_page_break_sentence_fragment_is_not_kept_as_heading(self):
        text = """## 1 故障分析
常见故障包括台区无电、主线无

## 电，以及支线无电。
后续分析正文。
"""
        cleaned = clean_journal(text)
        self.assertIn("电，以及支线无电。", cleaned)
        self.assertNotIn("## 电，以及支线无电。", cleaned)

    def test_page_break_fragment_drops_running_header_and_rejoins_sentence(self):
        text = """## 1 故障分析
常见故障包括台区无电、主线无

·１０１·

基于多源大数据的智能配电网停电故障分析 周鑫，等

## 电，以及支线无电。
后续分析正文。
"""
        cleaned = clean_common(clean_journal(text))
        self.assertIn("常见故障包括台区无电、主线无电,以及支线无电。", cleaned)
        self.assertNotIn("101", cleaned)
        self.assertNotIn("周鑫，等", cleaned)

    def test_picture_markers_are_removed(self):
        text = """## 1 正文
有效技术内容。
**==> picture [213 x 192] intentionally omitted <==**
**----- End of picture text -----**<br>
"""
        cleaned = clean_common(clean_journal(text))
        self.assertIn("有效技术内容", cleaned)
        self.assertNotIn("picture", cleaned.lower())

    def test_picture_end_marker_does_not_remove_ocr_table_text(self):
        text = """## 表1 台区参数
参数名称 取值<br>电压等级 /V 220<br>线路电阻 0.50<br>**----- End of picture text -----**<br>
"""
        cleaned = clean_common(clean_journal(text))
        self.assertIn("电压等级 /V 220", cleaned)
        self.assertIn("线路电阻 0.50", cleaned)
        self.assertNotIn("picture text", cleaned.lower())

    def test_inline_reference_heading_drops_only_reference_tail(self):
        text = """## 3 对策
采用无功补偿装置降低线损。 4 结语 技术结论保留。 参考文献
- [1] 无关引用题名。
"""
        cleaned = clean_journal(text)
        self.assertIn("技术结论保留", cleaned)
        self.assertNotIn("无关引用题名", cleaned)

    def test_corporate_regulation_keeps_chapters_and_articles(self):
        text = (
            "规章制度编号：国网（运检/4）312-2022 管理规定 "
            "第一章 总则 第一条 为规范配网故障抢修工作，制定本规定。 "
            "第二条 本规定适用于十千伏及以下配电网。 - 1 - "
            "第二章 管理要求 第三条 应及时开展故障研判和恢复送电。"
        )
        cleaned = clean_common(clean_regulation(text))
        self.assertTrue(cleaned.startswith("# 第一章 总则"))
        self.assertIn("## 第一条", cleaned)
        self.assertIn("# 第二章 管理要求", cleaned)
        self.assertIn("及时开展故障研判", cleaned)
        self.assertNotIn("- 1 -", cleaned)

    def test_corporate_regulation_does_not_split_inline_article_reference(self):
        text = (
            "第一章 总则 第一条 按照本规定第二条执行。 "
            "第二条 本规定自发布之日起施行。"
        )
        cleaned = clean_regulation(text)
        self.assertIn("按照本规定第二条执行", cleaned)
        self.assertEqual(cleaned.count("## 第二条"), 1)

    def test_regulation_accepts_code_styled_section_number(self):
        text = """封面信息
## `1` 范围
本文件规定了继电保护设备的设计要求。
## `2` 规范性引用文件
GB/T 14285 继电保护和安全自动装置技术规程
"""
        cleaned = clean_regulation(text)
        self.assertTrue(cleaned.startswith("## `1` 范围"))
        self.assertIn("继电保护设备的设计要求", cleaned)


if __name__ == "__main__":
    unittest.main()
