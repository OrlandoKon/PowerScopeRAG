import unittest

from pipeline.chunk import chunk_document


class ChunkDocumentTests(unittest.TestCase):
    def test_skips_markdown_table_separator_fragments(self):
        records = chunk_document(
            "# 第一章 总则\n\n|---|\n\n## 第一条\n\n本规定适用于配网抢修。",
            "sample.md",
            "regulation",
        )

        self.assertEqual(1, len(records))
        self.assertEqual("本规定适用于配网抢修。", records[0]["content"])

    def test_keeps_short_meaningful_clause(self):
        records = chunk_document("本规定自印发之日起施行。", "sample.md", "regulation")

        self.assertEqual(1, len(records))


if __name__ == "__main__":
    unittest.main()
