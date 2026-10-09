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
    def test_no_real_data_yet(self):
        s = check_updates.check(None, "2026-08")
        self.assertEqual([(a["kind"], a["key"]) for a in s["alerts"]], [("rebuild", "2026-08"), ("mdis", "2025")])

    def test_up_to_date_after_rebuild_but_mdis_pending(self):
        s = check_updates.check({"od_latest": "2024-12", "od_estimated_until": "2026-08"}, "2026-08")
        self.assertEqual([(a["kind"], a["key"]) for a in s["alerts"]], [("mdis", "2025")])

    def test_all_done_and_december_edge(self):
        self.assertEqual(check_updates.check({"od_latest": "2025-12", "od_estimated_until": "2026-08"}, "2026-08")["alerts"], [])
        s = check_updates.check({"od_latest": "2025-12", "od_estimated_until": "2026-12"}, "2026-12")
        self.assertEqual([a["key"] for a in s["alerts"]], ["2026"])   # 12월 공개 → 그해 연간자료 알림

    def test_kosis_failure_makes_no_alerts(self):
        self.assertEqual(check_updates.check(None, None)["alerts"], [])


class NotifyTest(unittest.TestCase):
    def setUp(self):
        import notify_issues
        self.n = notify_issues

    def status(self, cov, published="2026-08"):
        return check_updates.check(cov, published)

    def test_legacy_alert_closed_with_explanation_and_new_alerts_created(self):
        acts = self.n.plan([{"number": 1, "title": "인구이동 2026-08 자료 공개됨"}], self.status(None))
        self.assertEqual([a[:2] for a in acts], [("close", 1), ("create", "rebuild"), ("create", "mdis")])
        self.assertEqual(acts[0][3], "not_planned")

    def test_no_duplicates_and_close_when_resolved(self):
        open_ = [{"number": 2, "title": "데이터 갱신 필요: 인구이동 2026-08 공개"}, {"number": 3, "title": "MDIS 2025년 인구이동 연간자료 반영 필요"}]
        self.assertEqual(self.n.plan(open_, self.status(None)), [])
        acts = self.n.plan(open_, self.status({"od_latest": "2025-12", "od_estimated_until": "2026-08"}))
        self.assertEqual([(a[0], a[1], a[3]) for a in acts], [("close", 2, "completed"), ("close", 3, "completed")])

    def test_resolved_month_closes_as_completed_even_if_newer_exists(self):
        acts = self.n.plan([{"number": 2, "title": "데이터 갱신 필요: 인구이동 2026-08 공개"}],
                           self.status({"od_latest": "2025-12", "od_estimated_until": "2026-08"}, "2026-09"))
        self.assertEqual([(a[0], a[1]) for a in acts], [("create", "rebuild"), ("close", 2)])
        self.assertEqual(acts[1][3], "completed")

    def test_superseded_by_newer_month(self):
        acts = self.n.plan([{"number": 2, "title": "데이터 갱신 필요: 인구이동 2026-08 공개"}],
                           self.status({"od_latest": "2025-12", "od_estimated_until": "2026-07"}, "2026-09"))  # 08 미반영 상태에서 09 공개
        self.assertEqual([(a[0], a[1]) for a in acts], [("create", "rebuild"), ("close", 2)])
        self.assertEqual(acts[1][3], "not_planned")

    def test_kosis_failure_only_closes_legacy(self):
        open_ = [{"number": 1, "title": "인구이동 2026-08 자료 공개됨"}, {"number": 2, "title": "데이터 갱신 필요: 인구이동 2026-08 공개"}]
        self.assertEqual([a[1] for a in self.n.plan(open_, self.status(None, None))], [1])

    def test_apply_links_new_issue_in_comments(self):
        calls = []
        def call(m, p, b=None):
            calls.append((m, p, b))
            return {"number": 7 if "MDIS" in (b or {}).get("title", "") else 6} if m == "POST" and p.endswith("/issues") else {}
        open_ = [{"number": 1, "title": "인구이동 2026-08 자료 공개됨"}]
        self.n.apply(self.n.plan(open_, self.status(None)), "o/r", call, open_)
        self.assertEqual([c[:2] for c in calls[:2]], [("POST", "/repos/o/r/issues")] * 2)
        self.assertIn("#7", calls[2][2]["body"])   # 옛 알림 닫는 댓글이 MDIS 알림을 가리킨다
        self.assertEqual(calls[3], ("PATCH", "/repos/o/r/issues/1", {"state": "closed", "state_reason": "not_planned"}))


if __name__ == "__main__":
    unittest.main()
