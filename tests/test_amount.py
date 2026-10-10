"""Le montant de la position : il change les dollars et le coût de sortie, pas la perte en %."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
PID = "pt-susde-26nov2026"


@unittest.skipUnless((ROOT / "data" / "reports" / f"{PID}.json").exists(), "lancer d'abord : python pipeline.py --all --book")
class AmountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        cls.app = app
        cls.guard = patch("app.ensure_live_data")
        cls.guard.start()
        for p in app.available_positions():
            pid = p["id"]
            app._graphs[pid] = json.loads((ROOT / "data" / "graphs" / f"{pid}.json").read_text())
            app._reports[pid] = json.loads((ROOT / "data" / "reports" / f"{pid}.json").read_text())
            path = ROOT / "data" / "sim" / f"{pid}.json"
            app._sims[pid] = json.loads(path.read_text()) if path.exists() else None
        cls.ref = app.graph_for(PID)["position"]["size_usd"]

    @classmethod
    def tearDownClass(cls):
        cls.guard.stop()
        for d in (cls.app._graphs, cls.app._reports, cls.app._sims, cls.app._models):
            d.clear()

    def q(self, extra=""):
        return self.app.parse(f"pos={PID}&incident=issuer:ethena&p=30" + extra)

    def test_default_amount_is_reference_size(self):
        self.assertEqual(self.q()["amount"], self.ref)

    def test_crisis_cost_scales_with_amount(self):
        rep = self.app.report_for(PID)
        c_ref = self.app.exit_model(rep, self.q(), self.ref)
        c_big = self.app.exit_model(rep, self.q(f"&amount={2 * self.ref:.0f}"), self.ref)
        self.assertAlmostEqual(c_big.c1, min(1.0, 2 * c_ref.c1))

    def test_bigger_position_sells_a_smaller_share_in_crisis(self):
        D, M = self.app.D, self.app.M
        rep = self.app.report_for(PID)
        pm = self.app.pm_for(PID)
        st = M.stats(pm.losses("issuer:ethena", 0.30))
        x = [D.decide(st, self.app.exit_model(rep, self.q(f"&amount={a:.0f}"), self.ref))["x_star"]
             for a in (self.ref / 2, self.ref, 5 * self.ref)]
        self.assertGreater(x[0], x[1])
        self.assertGreater(x[1], x[2])

    def test_normal_regime_respects_market_capacity(self):
        rep = self.app.report_for(PID)
        small = self.app.exit_model(rep, self.q("&regime=normal"), self.ref)
        huge = self.app.exit_model(rep, self.q(f"&regime=normal&amount={20 * self.ref:.0f}"), self.ref)
        self.assertEqual(small.capacity, 1.0)
        self.assertLess(huge.capacity, 1.0)

    def test_page_shows_the_chosen_amount(self):
        html = self.app.render_page(f"pos={PID}&amount=10000000")
        self.assertIn("10,00 M$", html)
        self.assertIn('name="amount"', html)


if __name__ == "__main__":
    unittest.main()
