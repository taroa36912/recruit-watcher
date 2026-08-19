import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
from checker import heuristic_classify

SETTINGS = {
    "graduation_year": 2028,
    "positive_keywords": ["インターン", "応募受付中", "新卒採用"],
    "negative_keywords": ["募集終了", "受付終了"],
    "role_keywords": {"quant": ["クオンツ", "金融工学"]},
}
TARGET = {"category": "quant", "required_any": ["クオンツ"]}


class ClassifierTests(unittest.TestCase):
    def test_open(self):
        status, score, _, _ = heuristic_classify(
            "2028年卒 クオンツ インターン 応募受付中", TARGET, SETTINGS
        )
        self.assertEqual(status, "open")
        self.assertGreaterEqual(score, 7)

    def test_closed(self):
        status, _, _, _ = heuristic_classify(
            "2028年卒 クオンツ インターン 募集終了", TARGET, SETTINGS
        )
        self.assertNotEqual(status, "open")


if __name__ == "__main__":
    unittest.main()
