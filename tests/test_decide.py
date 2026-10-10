import unittest

import decide as D
import model as M
from schema import edge, evidence, node

EV = [evidence("test", "https://example.org", "2026-10-10T00:00:00Z")]


def fixture_graph():
    """Petit graphe PT : émetteur → actif → SY → position ; oracle → prêt → AMM → position."""
    nodes = [node("position:t", "PT test", "position"), node("protocol:sy", "SY", "protocol"),
             node("asset:x", "sUSDe", "asset"), node("issuer:e", "Ethena", "issuer"),
             node("venue:amm", "AMM", "venue"), node("lending:m", "Morpho", "lending"),
             node("oracle:o", "Oracle", "oracle"), node("chain:ethereum", "Ethereum", "chain")]
    edges = [edge("protocol:sy", "position:t", "backing", "verified_onchain", EV),
             edge("asset:x", "protocol:sy", "backing", "verified_onchain", EV),
             edge("issuer:e", "asset:x", "backing", "documented", EV),
             edge("venue:amm", "position:t", "exit_liquidity", "api", EV),
             edge("lending:m", "venue:amm", "collateral_liquidation", "api", EV, {"market_id": "0x1", "pressure_ratio": 2.0}),
             edge("oracle:o", "lending:m", "pricing", "api", EV, {"market_id": "0x1"}),
             edge("chain:ethereum", "position:t", "settlement", "documented", EV)]
    return {"position_id": "t", "position": {"id": "position:t", "label": "PT test", "size_usd": 1e6, "info": {}},
            "nodes": nodes, "edges": edges}


class DecideTests(unittest.TestCase):
    def setUp(self):
        self.cost = D.ExitCost(0.11)

    def stat(self, L, cvar=None):
        return {"expected": L, "cvar95": cvar if cvar is not None else L, "var95": L, "p_any_loss": 1}

    def test_tiny_loss_means_stay(self):
        d = D.decide(self.stat(0.0005), self.cost)
        self.assertLess(d["x_star"], 0.01)
        self.assertEqual(d["category"], "rester")

    def test_huge_loss_means_exit_all(self):
        d = D.decide(self.stat(0.5), self.cost)
        self.assertEqual(d["x_star"], 1.0)
        self.assertEqual(d["category"], "tout vendre")

    def test_closed_form_matches_grid(self):
        for L in (0.01, 0.03, 0.06, 0.12, 0.2):
            d = D.optimal_fraction(L, self.cost)
            grid = min(D.GRID, key=lambda x: self.cost(x) + (1 - x) * L)
            self.assertAlmostEqual(d["closed_form"], min(L / 0.22, 1.0), places=9)
            self.assertLessEqual(abs(d["x"] - grid), 0.011)

    def test_partial_beats_both_extremes(self):
        d = D.decide(self.stat(0.06), self.cost)
        s = d["strategies"]
        self.assertAlmostEqual(d["x_star"], 0.06 / 0.22, places=6)
        self.assertLess(s["partiel"]["expected"], s["rester"]["expected"])
        self.assertLess(s["partiel"]["expected"], s["tout_vendre"]["expected"])
        self.assertGreater(d["saving_vs_best_extreme"], 0)

    def test_costs_are_consistent(self):
        st = self.stat(0.04, 0.3)
        for x in (0, 0.3, 1):
            c = D.strategy_costs(x, st, self.cost)
            self.assertAlmostEqual(c["expected"], 0.11 * x * x + (1 - x) * 0.04)
            self.assertAlmostEqual(c["worst5"], 0.11 * x * x + (1 - x) * 0.3)

    def test_capacity_limits_the_sale(self):
        d = D.decide(self.stat(0.5), D.ExitCost(0.11, capacity=0.4))
        self.assertLessEqual(d["x_star"], 0.4)

    def test_measured_points_interpolate(self):
        c = D.ExitCost(0.004, shape="points", points=[(0.25, 0.0002), (0.5, 0.0005), (1.0, 0.004)])
        self.assertAlmostEqual(c(0.75), 0.00225)
        self.assertEqual(c(0), 0)


class ModelTests(unittest.TestCase):
    def test_strongest_path_multiplies_weights(self):
        g = fixture_graph()
        pm = M.PositionModel(g, "base")
        w = pm.weights
        expected = (w[("oracle:o", "lending:m")] * w[("lending:m", "venue:amm")] * w[("venue:amm", "position:t")])
        self.assertAlmostEqual(pm.impact["oracle:o"], expected)
        self.assertAlmostEqual(pm.impact["issuer:e"], 1.0)

    def test_pressure_ratio_uses_high_bound(self):
        g = fixture_graph()
        pm = M.PositionModel(g, "base")
        self.assertAlmostEqual(pm.weights[("lending:m", "venue:amm")], 0.30)

    def test_expected_loss_close_to_p_sev_impact(self):
        g = fixture_graph()
        pm = M.PositionModel(g, "base")
        st = M.stats(pm.losses("issuer:e", 0.5))
        bg = M.stats(pm.losses(None))["expected"]
        target = 0.5 * pm.params["issuer:e"]["sev"] * 1.0
        self.assertLess(abs(st["expected"] - bg - target), 0.01)

    def test_losses_grow_with_probability(self):
        g = fixture_graph()
        pm = M.PositionModel(g, "base")
        last = -1
        for p in (0, 0.2, 0.5, 0.9):
            e = M.stats(pm.losses("issuer:e", p))["expected"]
            self.assertGreaterEqual(e, last)
            last = e

    def test_sum_mode_is_at_least_max_and_capped(self):
        g = fixture_graph()
        a = M.PositionModel(g, "base", "max").impact
        b = M.PositionModel(g, "base", "sum").impact
        for k in a:
            self.assertGreaterEqual(b[k] + 1e-12, a[k])
            self.assertLessEqual(b[k], 1.0)

    def test_stability_sentence(self):
        rows = [{"p": k / 10, "category": "rester" if k < 3 else ("vente partielle" if k < 8 else "tout vendre")} for k in range(11)]
        s = D.stability_sentence(rows, 0.5)
        self.assertEqual((s["from_p"], s["to_p"], s["first_all_p"]), (0.3, 0.7, 0.8))


if __name__ == "__main__":
    unittest.main()
