import unittest
from dotmd_parser import llm


class TestExtractJson(unittest.TestCase):
    def test_fenced_block(self):
        raw = 'prose\n```json\n{"a": 1}\n```\ntrailing'
        self.assertEqual(llm.extract_json(raw), {"a": 1})

    def test_bare_json(self):
        self.assertEqual(llm.extract_json('{"b": 2}'), {"b": 2})

    def test_invalid_raises(self):
        with self.assertRaises(RuntimeError):
            llm.extract_json("not json at all")


if __name__ == "__main__":
    unittest.main()
