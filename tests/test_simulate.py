"""Simulateur de défaillance : AMM Pendle, trois scénarios, moteur générique, données manquantes, page."""
import copy
import json
import unittest
from pathlib import Path

from risk_engine import amm
from risk_engine import simulate as SIM

ROOT = Path(__file__).resolve().parent.parent
USDE = "asset:1:0x4c9edd5852cd905f086c759e8383e09bff1e68b3"
STETH = "asset:1:0xae7ab96520de3a18e5e111b5eaab095312d7fe84"
WETH = "asset:1:0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
LINEAR_FEED = "oracle:1:0xa572dad7db3d975e9ce780db1cf3bf959401cd35"   # primaire et secours l'utilisent
USDE_FEED = "oracle:1:0xa569d910839ae8865da8f8e70fffb0cba869f961"     # secours seulement
META = "oracle:1:0xf8b29e7e9c00ce531434775f7923cc9fbca01b08"
VENUE = "venue:pendle:1:0x47ad2cd1dd15739a7a035b9d3b7828d916fef77e"
WSTETH_VENUE = "venue:pendle:1:0x34280882267ffa6383b363e278b027be083bbe3b"


def load(pid):
    g = json.loads((ROOT / "data" / "graphs" / f"{pid}.json").read_text(encoding="utf-8"))
    ctx = json.loads((ROOT / "data" / "sim" / f"{pid}.json").read_text(encoding="utf-8"))
    return g, ctx


def run(pid, scenario, node, sev, hours=24, **kw):
    g, ctx = load(pid)
    return SIM.simulate(g, ctx, scenario, node, sev, hours, **kw)


def metric(res, key):
    return next(m for m in res["result"]["metrics"] if m["key"] == key)


def step(res, node):
    return [s for s in res["steps"] if s["node"] == node][-1]


@unittest.skipUnless((ROOT / "data" / "sim" / "morpho-ptsusde-borrow.json").exists(),
                     "lancer d'abord : python pipeline.py --all --offline")
class PendleAmmTests(unittest.TestCase):
    def setUp(self):
        _, ctx = load("pt-susde-26nov2026")
        self.mk = amm.PendleMarket.from_state(ctx["pendle"][VENUE])

    def test_matches_onchain_router_static(self):
        """La courbe transcrite reproduit RouterStatic.swapExactPtForSyStatic (cache on-chain)."""
        from risk_engine import sources as S
        S.set_offline(True)
        try:
            for units in (503551, 1007101, 2014202):
                onchain = S.pendle_static_sell_pt("0x47ad2cd1dd15739a7a035b9d3b7828d916fef77e", units)
                ours = self.mk.sell_pt(units)["sy_out"]
                # les deux relevés ont quelques minutes d'écart (blocs différents) : tolérance 1e-5
                self.assertAlmostEqual(ours / onchain, 1.0, delta=1e-5)
        finally:
            S.set_offline(False)

    def test_spot_matches_api_implied_apy(self):
        g, _ = load("pt-susde-26nov2026")
        venue = next(n for n in g["nodes"] if n["id"] == VENUE)
        apy = (1 / self.mk.spot_pt_to_asset()) ** (amm.YEAR / self.mk.t) - 1
        self.assertAlmostEqual(apy, venue["metrics"]["implied_apy"], delta=1e-5)

    def test_proportional_withdrawal_keeps_spot_and_cuts_depth(self):
        cut = self.mk.scaled(0.2)
        self.assertAlmostEqual(cut.spot_pt_to_asset(), self.mk.spot_pt_to_asset(), places=12)
        self.assertAlmostEqual(cut.max_pt_sellable(), 0.2 * self.mk.max_pt_sellable(), places=6)
        self.assertGreater(cut.sell_pt(500_000)["price_impact"], self.mk.sell_pt(500_000)["price_impact"])

    def test_market_proportion_limit(self):
        r = self.mk.sell_pt(5_035_506)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "MarketProportionTooHigh")


@unittest.skipUnless((ROOT / "data" / "sim" / "morpho-ptsusde-borrow.json").exists(),
                     "lancer d'abord : python pipeline.py --all --offline")
class PriceShockTests(unittest.TestCase):
    def test_usde_depeg_propagates_through_value_chain(self):
        res = run("pt-susde-26nov2026", "price_shock", USDE, 0.05)
        for nid in ("asset:1:0x9d39a5de30e57443bff2a8307a4256c8797a3497", "protocol:pendle-sy:1:0xbf98480425a29197e5d99d003017f63a1e595d02"):
            self.assertAlmostEqual(step(res, nid)["change_pct"], -0.05, places=9)
        m = metric(res, "market")
        self.assertAlmostEqual(m["loss_usd"], 100_000, places=3)
        self.assertEqual([p["to"] for p in res["path"]][-1], "position:pt-susde-26nov2026")

    def test_primary_oracle_ignores_depeg_before_challenge_timelock(self):
        """1 h : la décote linéaire ignore USDe → santé inchangée ; aucune sévérité ne liquide."""
        res = run("morpho-ptsusde-borrow", "price_shock", USDE, 0.05, hours=1)
        L = res["result"]["lending"]
        self.assertAlmostEqual(L["hf1"], L["hf0"], places=12)
        self.assertEqual(step(res, META)["status"], "no_effect")
        self.assertIsNone(res["liquidation_threshold"]["severity"])
        self.assertAlmostEqual(metric(res, "market")["loss_usd"], 0.05 * L["collateral_market0"], places=3)

    def test_meta_oracle_switches_to_backup_after_timelock(self):
        res = run("morpho-ptsusde-borrow", "price_shock", USDE, 0.05, hours=24)
        L = res["result"]["lending"]
        meta = step(res, META)["meta"]
        self.assertEqual(meta["using_after"], "secours")
        self.assertLess(L["hf1"], L["hf0"])
        self.assertAlmostEqual(L["hf1"] / L["hf0"], L["oracle_price1"] / L["oracle_price0"], places=12)
        self.assertAlmostEqual(L["ltv1"], L["ltv0"] * L["oracle_price0"] / L["oracle_price1"], places=12)
        self.assertFalse(L["eligible"])                           # 5 % ne suffit pas…
        thr = res["liquidation_threshold"]["severity"]
        self.assertTrue(0.05 < thr < 0.10)                         # …le seuil est entre 5 et 10 %
        above = run("morpho-ptsusde-borrow", "price_shock", USDE, thr + 0.001, with_threshold=False)
        below = run("morpho-ptsusde-borrow", "price_shock", USDE, thr - 0.001, with_threshold=False)
        self.assertTrue(above["result"]["lending"]["eligible"])
        self.assertFalse(below["result"]["lending"]["eligible"])

    def test_steth_crash_on_wsteth_pt(self):
        res = run("pt-wsteth-30dec2027", "price_shock", STETH, 0.20)
        self.assertAlmostEqual(metric(res, "market")["loss_usd"], 400_000, places=3)
        self.assertTrue(metric(res, "execution")["status"].startswith("calculé"))

    def test_debt_depeg_does_not_move_morpho_health(self):
        """Morpho compte la dette en unités d'USDC et l'oracle ne lit pas USDC/USD."""
        res = run("morpho-ptsusde-borrow", "price_shock", "asset:1:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", 0.05)
        L = res["result"]["lending"]
        self.assertAlmostEqual(L["hf1"], L["hf0"], places=12)
        self.assertLess(L["debt_usd1"], L["debt_usd0"])
        self.assertLess(metric(res, "market")["loss_usd"], 0)     # les fonds propres en USD augmentent


@unittest.skipUnless((ROOT / "data" / "sim" / "morpho-ptsusde-borrow.json").exists(),
                     "lancer d'abord : python pipeline.py --all --offline")
class OracleFailureTests(unittest.TestCase):
    def test_market_prices_unchanged_but_position_liquidatable(self):
        res = run("morpho-ptsusde-borrow", "oracle_failure", LINEAR_FEED, 0.10)
        L = res["result"]["lending"]
        self.assertAlmostEqual(L["market_price1"], L["market_price0"], places=12)
        self.assertAlmostEqual(metric(res, "market")["loss_usd"], 0.0, places=6)
        self.assertAlmostEqual(L["oracle_price1"] / L["oracle_price0"], 0.90, places=9)
        self.assertTrue(L["eligible"])
        # primaire et secours lisent le même flux : écart nul, pas de bascule
        self.assertEqual(step(res, META)["meta"]["using_after"], "primaire")

    def test_liquidation_follows_morpho_rules(self):
        res = run("morpho-ptsusde-borrow", "oracle_failure", LINEAR_FEED, 0.10)
        L = res["result"]["lending"]
        liq = L["liquidation"]
        lif = min(1.15, 1 / (1 - 0.3 * (1 - L["lltv"])))
        self.assertAlmostEqual(L["lif"], lif, places=12)
        self.assertAlmostEqual(liq["seized_units"], L["debt_units"] * lif / L["oracle_price1"], places=6)
        # perte = collatéral saisi (au prix de marché) − dette remboursée
        self.assertAlmostEqual(metric(res, "liquidation")["loss_usd"], liq["penalty_usd"], places=4)
        self.assertIn("SI la liquidation est exécutée", metric(res, "liquidation")["status"])

    def test_eligibility_is_not_realised_loss(self):
        res = run("morpho-ptsusde-borrow", "oracle_failure", LINEAR_FEED, 0.10)
        T = res["result"]["total"]
        self.assertGreater(T["loss_usd"], T["loss_without_liquidation"])
        self.assertAlmostEqual(T["loss_without_liquidation"],
                               sum(m["loss_usd"] for m in res["result"]["metrics"]
                                   if m.get("loss_usd") is not None and m["key"] != "liquidation"), places=6)

    def test_backup_feed_failure_depends_on_duration(self):
        short = run("morpho-ptsusde-borrow", "oracle_failure", USDE_FEED, 0.10, hours=1)
        long = run("morpho-ptsusde-borrow", "oracle_failure", USDE_FEED, 0.10, hours=24)
        self.assertFalse(short["result"]["lending"]["eligible"])
        self.assertTrue(long["result"]["lending"]["eligible"])

    def test_pt_holder_untouched_by_oracle_with_flags(self):
        res = run("pt-susde-26nov2026", "oracle_failure", USDE_FEED, 0.10)
        self.assertAlmostEqual(res["result"]["total"]["loss_usd"], 0.0, places=6)
        self.assertTrue(res["result"]["unknown"])            # ventes forcées possibles : signalées, non chiffrées
        self.assertTrue(all(not e["included_in_total"] for e in res["secondary_effects"]))


@unittest.skipUnless((ROOT / "data" / "sim" / "morpho-ptsusde-borrow.json").exists(),
                     "lancer d'abord : python pipeline.py --all --offline")
class LiquidityCrisisTests(unittest.TestCase):
    def test_reference_price_unchanged_exit_degraded(self):
        res = run("pt-susde-26nov2026", "liquidity_crisis", VENUE, 0.80)
        self.assertAlmostEqual(metric(res, "market")["loss_usd"], 0.0, places=6)
        ex0, ex1 = res["result"]["exit_before"], res["result"]["exit_after"]
        self.assertTrue(ex0["full_exit"])
        self.assertFalse(ex1["full_exit"])
        self.assertAlmostEqual(ex1["max_sellable_units"], 0.2 * ex0["max_sellable_units"], places=4)
        self.assertGreater(metric(res, "execution")["loss_usd"], 0)
        blocked = metric(res, "blocked")
        self.assertTrue(blocked["excluded_from_total"])
        self.assertNotIn("blocked", res["result"]["total"]["components"])

    def test_forced_exit_loss_grows_with_severity(self):
        """La perte « sortie forcée » (part bloquée comptée) doit croître avec la crise, pas l'inverse."""
        t20 = run("pt-susde-26nov2026", "liquidity_crisis", VENUE, 0.20)["result"]["total"]
        t80 = run("pt-susde-26nov2026", "liquidity_crisis", VENUE, 0.80)["result"]["total"]
        self.assertTrue(t20["full_exit_possible"])
        self.assertEqual(t20["loss_if_forced_exit_usd"], t20["loss_usd"])
        self.assertFalse(t80["full_exit_possible"])
        self.assertAlmostEqual(t80["loss_if_forced_exit_usd"], t80["loss_usd"] + t80["blocked_usd"], places=6)
        self.assertGreater(t80["loss_if_forced_exit_usd"], t20["loss_if_forced_exit_usd"])

    def test_moderate_crisis_full_exit_still_possible(self):
        res = run("pt-susde-26nov2026", "liquidity_crisis", VENUE, 0.10)
        ex1 = res["result"]["exit_after"]
        self.assertTrue(ex1["full_exit"])
        self.assertGreater(ex1["price_impact"], res["result"]["exit_before"]["price_impact"])

    def test_no_propagation_to_lending_oracle(self):
        """L'oracle Morpho ne lit pas ce pool (décote linéaire) : la santé ne bouge pas."""
        res = run("morpho-ptsusde-borrow", "liquidity_crisis", VENUE, 0.80)
        L = res["result"]["lending"]
        self.assertAlmostEqual(L["hf1"], L["hf0"], places=12)
        self.assertNotIn(META, res["affected"])
        self.assertFalse(res["result"]["exit_after"]["full_exit"])

    def test_thin_wsteth_pool(self):
        res = run("pt-wsteth-30dec2027", "liquidity_crisis", WSTETH_VENUE, 0.50)
        self.assertGreater(metric(res, "execution")["loss_usd"], 0)


@unittest.skipUnless((ROOT / "data" / "sim" / "morpho-ptsusde-borrow.json").exists(),
                     "lancer d'abord : python pipeline.py --all --offline")
class EngineTests(unittest.TestCase):
    def test_each_node_evaluated_once_even_with_two_paths(self):
        """Le flux linéaire atteint le méta-oracle par deux chemins (primaire et secours)."""
        res = run("morpho-ptsusde-borrow", "oracle_failure", LINEAR_FEED, 0.10)
        nodes = [s["node"] for s in res["steps"] if s["node"] != res["position"]["id"]]
        self.assertEqual(len(nodes), len(set(nodes)))
        self.assertAlmostEqual(step(res, META)["after"], 0.90, places=6)   # pas 0,81 : pas de double compte

    def test_total_is_sum_of_components(self):
        for args in (("morpho-ptsusde-borrow", "oracle_failure", LINEAR_FEED, 0.12),
                     ("morpho-ptsusde-borrow", "price_shock", USDE, 0.15),
                     ("pt-susde-26nov2026", "liquidity_crisis", VENUE, 0.5)):
            res = run(*args)
            T = res["result"]["total"]
            parts = [m["loss_usd"] for m in res["result"]["metrics"] if m["key"] in T["components"]]
            self.assertAlmostEqual(T["loss_usd"], sum(parts), places=6)
            self.assertAlmostEqual(T["after"], T["initial"] - T["loss_usd"], places=6)

    def test_every_traversed_edge_has_a_model(self):
        res = run("morpho-ptsusde-borrow", "price_shock", USDE, 0.05)
        self.assertTrue(res["edges"])
        for e in res["edges"]:
            for k in ("dependency", "transmission", "formula", "params", "source", "assumptions"):
                self.assertIn(k, e)

    def test_unmodelled_pool_reports_unknown_not_a_number(self):
        res = run("aave-v3-usdc", "price_shock", WETH, 0.20)
        self.assertAlmostEqual(res["result"]["total"]["loss_usd"], 0.0, places=6)
        self.assertTrue(any("Aave" in u for u in res["result"]["unknown"]))

    def test_reflexive_loop_handled(self):
        res = run("wsteth-hold", "price_shock", STETH, 0.20)
        self.assertAlmostEqual(metric(res, "market")["loss_usd"], 400_000, places=3)
        self.assertEqual(metric(res, "execution")["status"], "information insuffisante")

    def test_incompatible_scenario_rejected(self):
        g, ctx = load("pt-susde-26nov2026")
        with self.assertRaises(ValueError):
            SIM.simulate(g, ctx, "liquidity_crisis", USDE, 0.5)
        self.assertEqual(SIM.compatible_scenarios(g, USDE), ["price_shock"])
        self.assertEqual(SIM.compatible_scenarios(g, VENUE), ["liquidity_crisis"])

    def test_missing_data_gives_unknown_not_crash(self):
        g, ctx = load("morpho-ptsusde-borrow")
        ctx = copy.deepcopy(ctx)
        ctx["oracles"] = {}
        res = SIM.simulate(g, ctx, "oracle_failure", LINEAR_FEED, 0.10)
        self.assertIn("error", res["result"])
        self.assertTrue(res["result"]["unknown"])
        ctx2 = copy.deepcopy(load("pt-susde-26nov2026")[1])
        ctx2["pendle"] = {}
        res2 = SIM.simulate(load("pt-susde-26nov2026")[0], ctx2, "liquidity_crisis", VENUE, 0.8)
        self.assertEqual(metric(res2, "execution")["status"], "information insuffisante")

    def test_every_position_and_scenario_runs(self):
        for f in sorted((ROOT / "data" / "sim").glob("*.json")):
            g, ctx = load(f.stem)
            done = set()
            for n in g["nodes"]:
                for sc in SIM.compatible_scenarios(g, n["id"]):
                    if (sc, n["kind"]) in done and sc != "price_shock":
                        continue
                    done.add((sc, n["kind"]))
                    with self.subTest(pos=f.stem, node=n["id"], scenario=sc):
                        res = SIM.simulate(g, ctx, sc, n["id"], SIM.SCENARIOS[sc]["default"], with_threshold=False)
                        self.assertIn("result", res)


@unittest.skipUnless((ROOT / "data" / "sim" / "morpho-ptsusde-borrow.json").exists(),
                     "lancer d'abord : python pipeline.py --all --offline")
class SimulatorPageTests(unittest.TestCase):
    def test_page_runs_each_scenario(self):
        import app
        for node, sc, sev in ((USDE, "price_shock", 5), (LINEAR_FEED, "oracle_failure", 10), (VENUE, "liquidity_crisis", 80)):
            with self.subTest(sc):
                html = app.render_page(f"pos=morpho-ptsusde-borrow&snode={node}&stype={sc}&sev={sev}&run=1")
                self.assertIn("Sévérité financière", html)
                self.assertIn("Facteur de santé", html)
                self.assertIn("Chemin de propagation", html)

    def test_exact_amounts_and_blocked_value_shown(self):
        import app
        h20 = app.render_page(f"pos=pt-susde-26nov2026&snode={VENUE}&stype=liquidity_crisis&sev=20&run=1")
        self.assertIn("1 995 408 $", h20)                 # plus d'arrondi « 2,00 M$ » qui masquait la perte
        self.assertNotIn("Sortie complète IMPOSSIBLE", h20)
        h80 = app.render_page(f"pos=pt-susde-26nov2026&snode={VENUE}&stype=liquidity_crisis&sev=80&run=1")
        self.assertIn("Sortie complète IMPOSSIBLE", h80)
        self.assertIn("Perte si sortie forcée maintenant", h80)
        self.assertIn("Impact si sortie forcée maintenant", h80)

    def test_node_selection_and_bad_input(self):
        import app
        html = app.render_page(f"pos=pt-susde-26nov2026&snode={VENUE}")
        self.assertIn("Lancer la simulation", html)
        self.assertNotIn("Sévérité financière", html)
        for q in ("snode=inconnu&run=1", f"snode={USDE}&stype=liquidity_crisis&run=1", f"snode={USDE}&sev=abc&run=1",
                  "snode=chain:ethereum&run=1"):
            with self.subTest(q):
                self.assertIn("Simulateur de défaillance", app.render_page(q))

    def test_api(self):
        import app
        res = app.api_simulate(f"pos=morpho-ptsusde-borrow&node={USDE}&type=price_shock&severity=0.05")
        self.assertEqual(res["scenario"], "price_shock")
        self.assertIn("error", app.api_simulate("pos=inconnu&node=x"))
        self.assertIn("error", app.api_simulate(f"pos=pt-susde-26nov2026&node={USDE}&type=liquidity_crisis"))


if __name__ == "__main__":
    unittest.main()
