"""새 자료 공개 확인."""
import json, os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
from sources import kosis  # noqa: E402
import check_updates  # noqa: E402


class KosisTest(unittest.TestCase):
    def test_latest_month_from_prd_de(self):
        body = json.dumps([{"PRD_DE": "202607", "DT": "1"}, {"PRD_DE": "202608", "DT": "2"}]).encode()
        seen = []
        got = kosis.latest_month("K", getter=lambda url: (seen.append(url), body)[1])
        self.assertEqual(got, "2026-08")
        self.assertIn("newEstPrdCnt=1", seen[0])

    def test_error_and_empty_responses_raise(self):
        with self.assertRaises(RuntimeError):
            kosis.latest_month("K", getter=lambda url: b'{"err": "20", "errMsg": "key"}')
        with self.assertRaises(RuntimeError):
            kosis.latest_month("K", getter=lambda url: b"[]")


class CheckTest(unittest.TestCase):
    def test_new_month_detected(self):
        s = check_updates.check({"od_latest": "2026-05"}, "2026-08")
        self.assertTrue(s["od_new"]); self.assertIn("data/raw/mdis", s["message"])

    def test_up_to_date_and_unknown(self):
        self.assertFalse(check_updates.check({"od_latest": "2026-08"}, "2026-08")["od_new"])
        self.assertFalse(check_updates.check({"od_latest": "2026-05"}, None)["od_new"])  # 확인 실패는 알림 안 함
        self.assertTrue(check_updates.check(None, "2026-08")["od_new"])                # 실데이터 없음


if __name__ == "__main__":
    unittest.main()
