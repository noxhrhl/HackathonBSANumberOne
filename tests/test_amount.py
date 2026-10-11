"""Le montant de la position : il change les dollars affichés."""
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
        for d in (cls.app._graphs, cls.app._reports, cls.app._sims):
            d.clear()

    def q(self, extra=""):
        return self.app.parse(f"pos={PID}&incident=issuer:ethena&p=30" + extra)

    def test_default_amount_is_reference_size(self):
        self.assertEqual(self.q()["amount"], self.ref)

    def test_page_shows_the_chosen_amount(self):
        html = self.app.render_page(f"pos={PID}&amount=10000000")
        self.assertIn("10,00 M$", html)
        self.assertIn('name="amount"', html)


if __name__ == "__main__":
    unittest.main()
