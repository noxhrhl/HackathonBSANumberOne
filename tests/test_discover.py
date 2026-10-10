"""Vérifie la structure des graphes produits par un relevé antérieur."""
import json
import unittest
from pathlib import Path

from risk_engine import discover

ROOT = Path(__file__).resolve().parent.parent


def graph(pid):
    return json.loads((ROOT / "data" / "graphs" / f"{pid}.json").read_text(encoding="utf-8"))


class PtSusdeDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = graph("pt-susde-26nov2026")
        cls.ids = {n["id"]: n for n in cls.g["nodes"]}

    def test_graph_passes_all_checks(self):
        self.assertEqual(self.g["checks"]["errors"], [])

    def test_five_layers_of_belem_example_are_found(self):
        kinds = {n["kind"] for n in self.g["nodes"]}
        for k in ("chain", "asset", "issuer", "oracle", "venue", "lending"):
            self.assertIn(k, kinds)
        self.assertGreaterEqual(self.g["checks"]["coverage"]["max_depth"], 4)

    def test_pt_to_underlying_is_read_onchain(self):
        sy = "protocol:pendle-sy:1:0xbf98480425a29197e5d99d003017f63a1e595d02"
        e = next(e for e in self.g["edges"] if e["from"] == sy)
        self.assertEqual(e["status"], "verified_onchain")
        self.assertTrue(any(x["from"] == "asset:1:0x9d39a5de30e57443bff2a8307a4256c8797a3497" and x["to"] == sy
                            and x["status"] == "verified_onchain" for x in self.g["edges"]))

    def test_real_morpho_market_is_linked_with_its_id(self):
        mid = "0x5613b170ded221c588b6e9637b910f49e5508680ceede207f44aa72e5c46cb05"
        self.assertIn(f"lending:morpho:1:{mid}", self.ids)
        e = next(e for e in self.g["edges"] if e["from"] == f"lending:morpho:1:{mid}")
        self.assertEqual(e["details"]["market_id"], mid)
        self.assertGreater(e["details"]["pressure_ratio"], 1)

    def test_no_aave_edge_for_this_pt(self):
        pt_edges = [e for e in self.g["edges"] if e["from"].startswith("lending:aave") and e["to"].startswith("venue:")]
        self.assertEqual(pt_edges, [])

    def test_meta_oracle_is_decoded(self):
        o = self.ids["oracle:1:0xf8b29e7e9c00ce531434775f7923cc9fbca01b08"]
        self.assertEqual(o["details"]["type"], "meta_oracle")
        self.assertAlmostEqual(o["metrics"]["challenge_timelock_h"], 16.0)
        self.assertTrue(any(n.get("details", {}).get("type") == "pendle_linear_discount" for n in self.g["nodes"]))

    def test_ethena_hedge_venues_come_from_the_api(self):
        self.assertIn("perp:binance", self.ids)
        e = next(e for e in self.g["edges"] if e["from"] == "perp:binance")
        self.assertEqual(e["status"], "api")
        self.assertGreater(e["details"]["share_of_backing"], 0.05)


class OtherPositionsTests(unittest.TestCase):
    def test_wsteth_pt_depends_on_lido_not_ethena(self):
        g = graph("pt-wsteth-30dec2027")
        ids = {n["id"] for n in g["nodes"]}
        self.assertIn("issuer:lido", ids)
        self.assertNotIn("issuer:ethena", ids)
        self.assertEqual(g["checks"]["errors"], [])

    def test_aave_supply_sees_collateral_risk(self):
        g = graph("aave-v3-usdc")
        self.assertEqual(g["checks"]["errors"], [])
        bad = [e for e in g["edges"] if e["mechanism"] == "bad_debt"]
        self.assertGreaterEqual(len(bad), 3)
        self.assertTrue(all(e["details"]["market_id"] for e in bad))

    def test_all_positions_are_valid(self):
        for p in discover.POSITIONS["positions"]:
            with self.subTest(p["id"]):
                self.assertEqual(graph(p["id"])["checks"]["errors"], [])


if __name__ == "__main__":
    unittest.main()
