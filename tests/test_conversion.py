import unittest

from pipeline.convert import _normalize_custom_font_block, _private_use_count


class PdfConversionTests(unittest.TestCase):
    def test_private_use_glyphs_are_detected(self):
        self.assertEqual(2, _private_use_count("正文\ue5d2\ue5cf内容"))

    def test_custom_font_block_keeps_text_and_normalizes_numbering(self):
        raw = "２ \ue5d2．２ \ue5cf． １ 修正等值电阻法"
        normalized = _normalize_custom_font_block(raw)
        self.assertEqual("2.2.1 修正等值电阻法", normalized)


if __name__ == "__main__":
    unittest.main()
