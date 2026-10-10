"""Tests des règles de liquidation et de la séparation oracle / vente."""

import copy
import json
import unittest
from pathlib import Path

from risk_engine.cascade import Scenario, simulate


def fixture(debts=(0.79,), collateral=(1.0,), quote_price=1.2):
    """Petit marché synthétique réservé aux tests, jamais affiché comme observé."""
    borrowers = [{"address": f"0x{i:040x}", "debt_raw": str(round(d * 1e6)),
                  "collateral_raw": str(round(c * 1e18))}
                 for i, (d, c) in enumerate(zip(debts, collateral), 1)]
    return {"schema_version": 1, "collected_at": "TEST SYNTHÉTIQUE", "market_id": "0xmarket",
            "market": {"marketId": "0xmarket", "lltv": str(round(.8 * 1e18)),
                       "loanAsset": {"symbol": "USDC", "decimals": 6},
                       "collateralAsset": {"decimals": 18},
                       "oracle": {"address": "0xoracle"},
                       "state": {"borrowAssets": sum(int(x["debt_raw"]) for x in borrowers),
                                 "collateralAssets": str(sum(int(x["collateral_raw"]) for x in borrowers))}},
            "oracle": {"address": "0xoracle", "price_usdc_per_pt": 1.0},
            "borrowers": borrowers,
            "pendle": {"quotes": [{"size_pt": n, "ok": True,
                                   "proceeds_usdc_assumed": n * quote_price} for n in (1, 2, 3)]}}


class CascadeTests(unittest.TestCase):
    def test_threshold_crossing(self):
        safe = simulate(fixture(), Scenario(oracle_shock=0, sale_shock=0, gas_usdc=0))
        stressed = simulate(fixture(), Scenario(oracle_shock=.05, sale_shock=0, gas_usdc=0))
        self.assertEqual(safe["liquidated_borrowers"], 0)
        self.assertEqual(stressed["liquidated_borrowers"], 1)

    def test_sale_price_alone_does_not_move_oracle_or_trigger(self):
        result = simulate(fixture(), Scenario(oracle_shock=0, sale_shock=.5, gas_usdc=0))
        self.assertEqual(result["final_oracle_price"], 1.0)
        self.assertEqual(result["liquidated_borrowers"], 0)
        self.assertEqual(result["steps"], [])

    def test_insufficient_liquidity_limits_sales_and_leaves_exposure(self):
        result = simulate(fixture(debts=(.79, .79), collateral=(1, 1)),
                          Scenario(oracle_shock=.05, sale_shock=0, liquidity_fraction=.1, gas_usdc=0))
        self.assertLessEqual(result["sold_pt"], result["quote_capacity_pt"] + 1e-8)
        self.assertGreater(result["exposed_borrowers_remaining"], 0)
        self.assertEqual(result["realized_bad_debt_usdc"], 0)

    def test_unprofitable_liquidation_is_skipped(self):
        result = simulate(fixture(quote_price=.5), Scenario(oracle_shock=.05, sale_shock=0, gas_usdc=.01))
        self.assertEqual(result["liquidated_borrowers"], 0)
        self.assertEqual(result["realized_bad_debt_usdc"], 0)
        self.assertEqual(result["steps"][0]["skipped"][0]["reason"], "liquidation non rentable")

    def test_debt_and_collateral_conservation_with_bad_debt(self):
        result = simulate(fixture(debts=(.95,), collateral=(1,)),
                          Scenario(oracle_shock=0, sale_shock=0, gas_usdc=0))
        self.assertGreater(result["realized_bad_debt_usdc"], 0)
        self.assertAlmostEqual(result["initial_debt_usdc"], result["repaid_usdc"] +
                               result["realized_bad_debt_usdc"] + result["remaining_debt_usdc"], places=8)
        self.assertAlmostEqual(result["initial_collateral_pt"], result["sold_pt"] +
                               result["remaining_collateral_pt"], places=8)

    def test_reproducibility_and_no_mutation(self):
        snap = fixture(debts=(.79, .77), collateral=(1, 1))
        before = copy.deepcopy(snap)
        scenario = Scenario(oracle_shock=.05, sale_shock=.01, gas_usdc=0)
        self.assertEqual(simulate(snap, scenario), simulate(snap, scenario))
        self.assertEqual(snap, before)

    def test_backup_oracle_requires_a_new_assumption(self):
        snap = fixture()
        snap["oracle"]["current_is_primary"] = False
        with self.assertRaisesRegex(ValueError, "Oracle de secours"):
            simulate(snap)

    def test_observed_snapshot_coverage_if_present(self):
        path = Path(__file__).resolve().parent.parent / "data/cascade/morpho-ptsusde-usdc.json"
        if not path.exists():
            self.skipTest("instantané Morpho non collecté")
        result = simulate(json.loads(path.read_text()))
        self.assertGreater(result["total_borrowers"], 0)
        self.assertLessEqual(result["sold_pt"], result["quote_capacity_pt"] + 1e-8)


if __name__ == "__main__":
    unittest.main()
