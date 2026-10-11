"""Règle « rester ou sortir » sans probabilité (risk_engine/exit_rule.py, SIMULATION_MODEL.md § 14)."""
import json
import unittest
from pathlib import Path

from risk_engine import exit_rule as X
from risk_engine import simulate as SIM

ROOT = Path(__file__).resolve().parent.parent


def load(pid):
    return (json.loads((ROOT / "data" / "graphs" / f"{pid}.json").read_text(encoding="utf-8")),
            json.loads((ROOT / "data" / "sim" / f"{pid}.json").read_text(encoding="utf-8")))


def node(g, prefix):
    return next(n["id"] for n in g["nodes"] if n["id"].startswith(prefix))


@unittest.skipUnless((ROOT / "data" / "sim" / "pt-susde-26nov2026.json").exists(), "fixtures de simulation absentes")
class ExitRuleTests(unittest.TestCase):
    def test_every_compatible_node_runs(self):
        for pid in ("pt-susde-26nov2026", "pt-wsteth-30dec2027", "aave-v3-usdc", "morpho-ptsusde-usdc", "morpho-ptsusde-borrow"):
            g, ctx = load(pid)
            for n in g["nodes"]:
                for s in SIM.compatible_scenarios(g, n["id"]):
                    d = X.decide(g, ctx, s, n["id"], SIM.SCENARIOS[s]["default"])
                    if d["ok"]:
                        self.assertLessEqual(d["best"]["total"], d["stay"] + 1e-6, (pid, n["id"], s))
                        self.assertIn(d["category"], ("rester", "partiel", "tout", "inconnu"))

    def test_pt_price_shock_sells_when_exit_is_cheaper(self):
        g, ctx = load("pt-susde-26nov2026")
        d = X.decide(g, ctx, "price_shock", node(g, "asset:1:0x9d39a5de30e57443bff2"), 0.05)
        self.assertTrue(d["ok"])
        self.assertAlmostEqual(d["stay"], 0.05 * ctx["position"]["size_usd"], delta=1.0)
        self.assertGreater(d["best"]["x"], 0)
        self.assertLess(d["best"]["total"], d["stay"])
        # coût de vente croissant avec la quantité vendue (courbe de l'AMM)
        ex = [r["exec"] for r in d["rows"]]
        self.assertEqual(ex, sorted(ex))

    def test_borrower_partial_sale_repays_debt(self):
        g, ctx = load("morpho-ptsusde-borrow")
        d = X.decide(g, ctx, "oracle_failure", node(g, "oracle:1:0xf8b29e7e9c00ce53143"), 0.10)
        self.assertTrue(d["ok"])
        self.assertEqual(d["category"], "partiel")
        self.assertLess(d["best"]["total"], d["all"]["total"])

    def test_lender_capped_by_liquidity(self):
        g, ctx = load("morpho-ptsusde-usdc")
        d = X.decide(g, ctx, "protocol_exploit", node(g, "lending:morpho:1:0x5613b170ded"), 0.10)
        self.assertTrue(d["ok"])
        self.assertLessEqual(d["rows"][-1]["x"], d["cap"] + 1e-9)
        self.assertEqual(d["kind"], "retirer")

    def test_amount_changes_decision_inputs(self):
        import app
        g, ctx = load("pt-wsteth-30dec2027")
        nid = node(g, "asset:1:0x7f39c581f595b53c5cb1")
        small = X.decide(g, app.scaled_ctx(ctx, 0.1), "price_shock", nid, 0.05)
        big = X.decide(g, app.scaled_ctx(ctx, 1.0), "price_shock", nid, 0.05)
        # plus la position est grosse, plus la vente glisse : part vendue optimale plus petite ou égale
        self.assertGreaterEqual(small["best"]["x"], big["best"]["x"])


if __name__ == "__main__":
    unittest.main()
