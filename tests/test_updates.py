"""새 자료 공개 확인."""
import pathlib, tempfile
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

    def test_reb_alert_when_trade_ahead_of_buyer_residence(self):
        """R-ONE은 로그인이 필요해 사람이 받아 넣는다. 실거래가 앞서가면 이슈로 알린다."""
        done = {"od_latest": "2025-12", "od_estimated_until": "2026-08"}
        s = check_updates.check({**done, "trade_latest": "2026-10", "reb_latest": "2026-08"}, "2026-08")
        self.assertEqual([(a["kind"], a["key"]) for a in s["alerts"]], [("reb", "2026-10")])
        self.assertIn("data/raw/r-one/", s["alerts"][0]["body"])
        self.assertEqual(s["resolved"]["reb"], "2026-08")
        # 따라잡으면 알림이 없다
        self.assertEqual(check_updates.check({**done, "trade_latest": "2026-10", "reb_latest": "2026-10"}, "2026-08")["alerts"], [])
        # 한쪽이 없으면 판단하지 않는다
        self.assertEqual(check_updates.check({**done, "trade_latest": "2026-10"}, "2026-08")["alerts"], [])


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

    def test_reb_issue_closed_when_caught_up(self):
        import notify_issues as ni
        opened = [{"number": 9, "title": "R-ONE 매입자거주지 2026-10 반영 필요"}]
        # 아직 안 따라잡음 → 그대로 둔다
        acts = ni.plan(opened, {"kosis_ok": True, "alerts": [{"kind": "reb", "key": "2026-10", "title": opened[0]["title"], "body": "b"}],
                                "resolved": {"reb": "2026-08"}})
        self.assertEqual(acts, [])
        # 따라잡음 → 닫는다
        acts = ni.plan(opened, {"kosis_ok": True, "alerts": [], "resolved": {"reb": "2026-10"}})
        self.assertEqual([(a[0], a[1], a[3]) for a in acts], [("close", 9, "completed")])

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

class InterimModeTest(unittest.TestCase):
    """demo 와 real 이 data/interim 을 공유해 조용히 섞이는 것을 막는다."""
    def test_mode_switch_clears_csvs_and_keeps_others(self):
        import run
        with tempfile.TemporaryDirectory() as d:
            for n in ("migration_od_month.csv", "card_month.csv"):
                pathlib.Path(os.path.join(d, n)).write_text("x", encoding="utf-8")
            pathlib.Path(os.path.join(d, "mdis_run.log")).write_text("keep", encoding="utf-8")
            run.interim_mode(d, "demo")
            self.assertTrue(os.path.exists(os.path.join(d, "migration_od_month.csv")))  # 첫 호출은 비우지 않는다
            run.interim_mode(d, "demo")
            self.assertTrue(os.path.exists(os.path.join(d, "card_month.csv")))           # 같은 모드면 유지
            run.interim_mode(d, "real")
            self.assertFalse(os.path.exists(os.path.join(d, "migration_od_month.csv")))  # 모드가 바뀌면 비운다
            self.assertFalse(os.path.exists(os.path.join(d, "card_month.csv")))
            self.assertTrue(os.path.exists(os.path.join(d, "mdis_run.log")))             # csv 아닌 건 남긴다
            self.assertEqual(pathlib.Path(os.path.join(d, ".mode")).read_text(encoding="utf-8"), "real")

class KosisRetryTest(unittest.TestCase):
    """KOSIS 는 6개월씩 나눠 여러 번 호출한다. 한 번 끊겼다고 전체가 날아가면 안 된다."""
    def test_retries_then_succeeds(self):
        calls = []

        class R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"ok"

        def flaky(url, timeout=None):
            calls.append(url)
            if len(calls) < 3:
                raise ConnectionResetError(54, "Connection reset by peer")
            return R()

        orig_open, orig_sleep = kosis.urllib.request.urlopen, kosis.time.sleep
        kosis.urllib.request.urlopen, kosis.time.sleep = flaky, lambda s: None
        try:
            self.assertEqual(kosis.http_get("https://example.test/x"), b"ok")
            self.assertEqual(len(calls), 3)
            kosis.urllib.request.urlopen = lambda url, timeout=None: (_ for _ in ()).throw(ConnectionResetError(54, "x"))
            with self.assertRaises(ConnectionResetError):   # 끝까지 실패하면 숨기지 않고 올린다
                kosis.http_get("https://example.test/y")
        finally:
            kosis.urllib.request.urlopen, kosis.time.sleep = orig_open, orig_sleep



if __name__ == "__main__":
    unittest.main()
