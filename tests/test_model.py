"""흐름 추정 모형의 불변식. 추정치는 '그럴듯함'이 아니라 반드시 성립해야 하는 조건으로 검증한다."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
import build_flows as bf  # noqa: E402
import leverage as lv  # noqa: E402

CODES = ["11680", "11650", "11440", "41135", "41465", "28185"]
NC = "00000"


def nodes():
    ns = [dict(code=c, name=c, short=c, sido={"11": "서울", "41": "경기", "28": "인천"}[c[:2]], ax=0, ay=0, rings=[]) for c in CODES]
    return ns + [dict(code=NC, name="비수도권", short="지방", sido="지방", ax=0, ay=0, rings=[])]


def tables(od_months=("2025-01", "2025-02", "2025-03"), trade_months=("2025-01", "2025-02", "2025-03")):
    trade, rent, share = {}, {}, {}
    for ym in trade_months:
        for k, c in enumerate(CODES):
            n = 100 + 10 * k
            trade[(ym, c)] = dict(n=n, value=n * 10.0, corp_n=3, corp_value=30.0, equity=n * 6.0)
            rent[(ym, c)] = dict(contracts=200, new=120, deposit=200 * 5.0)
            share[(ym, c)] = ({"same_sgg": .4, "same_sido": .35, "seoul": 0, "other": .25} if c.startswith("11")
                              else {"same_sgg": .5, "same_sido": .15, "seoul": .3, "other": .05})
    od = {}
    for ym in od_months:
        # 강남 → 분당이 압도적인 이주 패턴
        od[ym] = {("11680", "41135"): 500, ("11650", "41135"): 50, ("11440", "41135"): 5,
                  ("41465", "41135"): 80, ("28185", "41135"): 10, (NC, "41135"): 20,
                  ("11650", "11680"): 300, ("11440", "11680"): 40, ("41135", "11680"): 60, (NC, "11680"): 30,
                  ("11680", "11680"): 900, ("41135", "41135"): 700}
    return dict(trade=trade, rent=rent, share=share, od=od)


class EstimateTest(unittest.TestCase):
    def setUp(self):
        self.t = tables()
        self.mats = bf.estimate(self.t, "2025-03", CODES, ["2025-01", "2025-02", "2025-03"])

    def inflow(self, layer, j, origins=None):
        return sum(v[0] for (a, b), v in self.mats[layer].items() if b == j and (origins is None or a in origins))

    def test_trade_inflow_equals_reported_trades(self):
        for c in CODES:
            self.assertAlmostEqual(self.inflow("trade", c), self.t["trade"][("2025-03", c)]["n"], places=6)

    def test_seoul_bucket_matches_reb_share(self):
        seoul = {c for c in CODES if c.startswith("11")}
        n = self.t["trade"][("2025-03", "41135")]["n"]
        self.assertAlmostEqual(self.inflow("trade", "41135", seoul), n * 0.3, places=6)

    def test_origin_follows_migration_pattern(self):
        m = self.mats["trade"]
        self.assertGreater(m[("11680", "41135")][0], 5 * m[("11440", "41135")][0])

    def test_equity_layer_same_counts_and_totals(self):
        for c in CODES:
            self.assertAlmostEqual(self.inflow("equity", c), self.inflow("trade", c), places=6)
            eq = sum(v[1] for (a, b), v in self.mats["equity"].items() if b == c)
            self.assertAlmostEqual(eq, self.t["trade"][("2025-03", c)]["equity"], places=4)

    def test_rent_inflow_equals_new_contracts(self):
        for c in ("41135", "11680"):
            self.assertAlmostEqual(self.inflow("rent", c), 120, places=6)

    def test_no_negative_values(self):
        for layer in self.mats.values():
            for c, v in layer.values():
                self.assertGreaterEqual(c, 0); self.assertGreaterEqual(v, 0)


class OdLagTest(unittest.TestCase):
    def test_window_uses_latest_available(self):
        od = ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05"]
        self.assertEqual(bf.od_window(od, "2025-08", 3), ["2025-03", "2025-04", "2025-05"])
        self.assertEqual(bf.od_window(od, "2024-06", 2), ["2025-01", "2025-02"])

    def test_lagged_month_does_not_collapse_to_uniform(self):
        t = tables(od_months=("2025-01",), trade_months=("2025-01", "2025-03"))
        data = bf.build(t, nodes(), demo=True)
        self.assertEqual(data["meta"]["od_missing"], ["2025-03"])
        self.assertEqual(data["meta"]["od_window"]["2025-03"], ["2025-01", "2025-01"])
        jan = {(f[0], f[1]): f[2] for f in data["months"]["2025-01"]["layers"]["trade"]["flows"]}
        mar = {(f[0], f[1]): f[2] for f in data["months"]["2025-03"]["layers"]["trade"]["flows"]}
        self.assertEqual(jan, mar)  # 같은 거래량·비중이면 같은 배분이어야 한다
        self.assertEqual(data["months"]["2025-03"]["layers"]["move"]["flows"], [])

    def test_empty_od_fails_loudly(self):
        with self.assertRaises(ValueError):
            bf.build(tables(od_months=()), nodes(), demo=True)


class SummaryTest(unittest.TestCase):
    def test_in_equals_out_and_small_nodes_kept(self):
        data = bf.build(tables(), nodes(), demo=True)
        for layer in ("trade", "rent", "move"):
            st = data["months"]["2025-03"]["layers"][layer]["stats"]
            self.assertAlmostEqual(sum(s[0] for s in st), sum(s[1] for s in st), delta=1)
        flows = data["months"]["2025-03"]["layers"]["trade"]["flows"]
        idx = CODES.index("28185")
        self.assertTrue(any(f[0] == idx or f[1] == idx for f in flows))


class LeverageTest(unittest.TestCase):
    def test_policy_steps(self):
        self.assertAlmostEqual(lv.equity(30, "11680", "2025-05"), 30 - 0.65 * 15)       # LTV 50%
        self.assertAlmostEqual(lv.equity(30, "11680", "2025-08"), 30 - 0.65 * 6)        # 6·27 한도 6억
        self.assertAlmostEqual(lv.equity(30, "11680", "2025-12"), 30 - 0.65 * 2)        # 25억 초과 2억
        self.assertAlmostEqual(lv.equity(5, "41590", "2025-12"), 5 - 0.65 * 3.5)        # 비규제 LTV 70%, 한도 미달
        self.assertFalse(lv.regulated("11440", "2025-10"))
        self.assertTrue(lv.regulated("11440", "2025-11"))

    def test_equity_bounds(self):
        for p in (1, 5, 14.9, 15.1, 24.9, 25.1, 60):
            for code in ("11680", "41590"):
                for ym in ("2025-01", "2025-08", "2026-01"):
                    e = lv.equity(p, code, ym)
                    self.assertTrue(0 < e <= p, (p, code, ym, e))


if __name__ == "__main__":
    unittest.main()
