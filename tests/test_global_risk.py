"""Vérifications de l'onglet global sans aucun appel réseau."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

from global_risk import analyze
from global_risk import web

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = [{"source": "API test", "url": "https://example.org/test", "fetched_at": "2026-10-10T10:00:00Z"}]


def fixture_graph():
    """Ethena → USDe → sUSDe, plus deux canaux de prix indépendants."""
    def node(nid, kind):
        return {"id": nid, "name": nid, "kind": kind, "metrics": {}, "evidence": EVIDENCE}

    def edge(src, dst, mechanism):
        return {"from": src, "to": dst, "mechanism": mechanism,
                "status": "api", "evidence": EVIDENCE}

    return {"position_id": "test", "position": {"id": "position:test", "label": "Position test",
                                             "size_usd": 1000},
            "nodes": [node("position:test", "position"), node("ethena", "issuer"),
                      node("usde", "asset"), node("susde", "asset"),
                      node("oracle", "oracle"), node("venue", "venue")],
            "edges": [edge("ethena", "usde", "backing"), edge("usde", "susde", "backing"),
                      edge("susde", "position:test", "backing"),
                      edge("oracle", "position:test", "pricing"),
                      edge("venue", "position:test", "exit_liquidity")],
            "meta": {"sources": EVIDENCE}, "checks": {"errors": [], "coverage": {"dependencies": 5}}}


REPORT = {"horizon_days": 30, "data_fetched_at": {"newest": "2026-10-10T10:00:00Z"},
          "exit": {"quotes": {"source": "API test", "quotes": []}}}


class GlobalRiskTests(unittest.TestCase):
    def test_without_selected_incident_covers_every_node(self):
        graph = fixture_graph()
        result = analyze(graph, REPORT, {"missing": []})
        self.assertEqual(set(result["covered_node_ids"]),
                         {n["id"] for n in graph["nodes"] if n["kind"] != "position"})
        self.assertEqual(result["position_id"], "test")
        self.assertEqual(result["horizon_days"], 30)
        self.assertIn("Prêts et liquidations", result["absent_groups"])

    def test_chain_does_not_create_a_total_by_adding_nodes(self):
        result = analyze(fixture_graph(), REPORT)
        by_id = {r["id"]: r for r in result["rows"]}
        self.assertEqual(by_id["ethena"]["path"],
                         ["ethena", "usde", "susde", "position:test"])
        self.assertIsNone(result["summary"]["quantified_global_risk"])
        self.assertLessEqual(result["summary"]["largest_single_event_fraction"], 1)
        self.assertIn("corrélées", result["summary"]["reason"])

    def test_missing_data_is_visible_and_not_filled(self):
        graph = fixture_graph()
        next(n for n in graph["nodes"] if n["id"] == "oracle")["evidence"] = []
        next(e for e in graph["edges"] if e["from"] == "oracle")["evidence"] = []
        result = analyze(graph, REPORT, {"missing": ["price() Morpho indisponible"]})
        oracle = next(r for r in result["rows"] if r["id"] == "oracle")
        self.assertIsNone(oracle["probability"])
        self.assertEqual(oracle["sources"], [])
        self.assertEqual(result["confidence"], "faible")
        self.assertTrue(any("price() Morpho indisponible" in x for x in result["missing"]))
        html = web.render_global(result, graph, REPORT, {}, [{"id": "test", "label": "Position test"}])
        self.assertIn("Données indisponibles", html)

    def test_ranking_uses_expected_loss(self):
        result = analyze(fixture_graph(), REPORT)
        rows = result["rows"]
        for r in rows:
            if r["probability"] is None:
                self.assertIsNone(r["expected_loss_fraction"])
            else:
                self.assertAlmostEqual(r["expected_loss_fraction"], r["probability"] * r["conditional_loss_fraction"])
        known = [r["expected_loss_fraction"] for r in rows if r["expected_loss_fraction"] is not None]
        self.assertEqual(known, sorted(known, reverse=True))
        first_unknown = next((i for i, r in enumerate(rows) if r["expected_loss_fraction"] is None), len(rows))
        self.assertTrue(all(r["expected_loss_fraction"] is None for r in rows[first_unknown:]))  # sans probabilité : après
        self.assertEqual(result["summary"]["largest_expected_node"], rows[0]["name"])
        self.assertIsNone(result["summary"]["quantified_global_risk"])                              # toujours pas de total

    def test_reproducible_with_fixed_horizon(self):
        graph = fixture_graph()
        self.assertEqual(analyze(graph, REPORT, horizon=45), analyze(graph, REPORT, horizon=45))

    def test_chosen_amount_scales_dollars_without_changing_risk_percentages(self):
        graph = fixture_graph()
        small = analyze(graph, REPORT, amount=1000)
        large = analyze(graph, REPORT, amount=3000)
        small_rows = {r["id"]: r for r in small["rows"]}
        large_rows = {r["id"]: r for r in large["rows"]}
        self.assertEqual(large["position_size_usd"], 3000)
        self.assertEqual(large["reference_size_usd"], 1000)
        for nid in small_rows:
            self.assertEqual(small_rows[nid]["conditional_loss_fraction"],
                             large_rows[nid]["conditional_loss_fraction"])
            self.assertAlmostEqual(large_rows[nid]["conditional_loss_usd"],
                                   3 * small_rows[nid]["conditional_loss_usd"])

    def test_oracle_and_sale_channels_remain_distinct(self):
        graph = fixture_graph()
        result = analyze(graph, REPORT)
        channels = {r["id"]: r["channel"] for r in result["rows"]}
        self.assertEqual(channels["oracle"], "oracle")
        self.assertEqual(channels["venue"], "sale")
        html = web.render_global(result, graph, REPORT, {"oracles": {"oracle": {"raw": 123}}},
                                 [{"id": "test", "label": "Position test"}])
        self.assertIn("Prix de l’oracle", html)
        self.assertIn("Prix de vente / liquidité", html)
        self.assertNotIn("Prix de vente et prix d'oracle", html)

    def test_global_page_shows_three_priorities_and_node_details(self):
        graph = fixture_graph()
        result = analyze(graph, REPORT)
        html = web.render_global(result, graph, REPORT, {},
                                 [{"id": "test", "label": "Position test"}])
        self.assertEqual(html.count('class="global-priority"'), 3)
        for index, row in enumerate(result["adverse_scenarios"][:3], 1):
            anchor = web._anchor(row["id"])
            self.assertIn(f'Risque {index:02d} · {row["group"]}', html)
            self.assertIn(f'data-risk-id="{anchor}"', html)
        for row in result["rows"]:
            anchor = web._anchor(row["id"])
            self.assertIn(f'href="#{anchor}"', html)
            self.assertEqual(html.count(f' id="{anchor}"'), 1)
        self.assertIn('id="global-risk-detail-content"', html)
        self.assertIn('class="global-all"', html)
        self.assertIn('class="global-family"', html)
        self.assertIn('selectRisk(link.getAttribute("href").slice(1), true)', html)
        self.assertIn("leurs pertes ne s'additionnent pas", html)
        for removed_section in ("Sensibilité aux hypothèses", "Prix de vente et prix d'oracle",
                                "Sources, données manquantes et limites"):
            self.assertNotIn(f'<h2>{removed_section}</h2>', html)

    def test_existing_view_is_rendered_by_existing_function(self):
        import app
        original = '<html><head></head><body><div class="tabs-wrap">ANCIEN_SITE</div></body></html>'
        decorated = web.add_tabs_to_current(original, "test")
        self.assertIn("ANCIEN_SITE", decorated)
        self.assertIn("Analyse globale des risques", decorated)
        self.assertIn("/global?pos=test", decorated)
        self.assertIn("/?pos=test", decorated)
        self.assertTrue(callable(app.render_page))
        self.assertTrue(callable(app.live_analysis))

    def test_existing_graph_fixture_is_fully_covered(self):
        path = ROOT / "data" / "graphs" / "pt-susde-26nov2026.json"
        report_path = ROOT / "data" / "reports" / "pt-susde-26nov2026.json"
        if not (path.exists() and report_path.exists()):
            self.skipTest("Fixtures du projet absentes")
        graph = json.loads(path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        result = analyze(graph, report, horizon=30)
        self.assertEqual(len(result["rows"]), len(graph["nodes"]) - 1)

    def test_live_collection_failure_has_no_snapshot_fallback(self):
        import app
        with patch.object(app, "ensure_live_data", side_effect=ConnectionError("source absente")):
            with self.assertRaises(ConnectionError):
                app.live_analysis("test", force=True)
        page = web.render_unavailable("test", ConnectionError("source absente"))
        self.assertIn("Données indisponibles", page)
        self.assertIn("Aucune ancienne donnée", page)


class UnifiedCurrentPageTests(unittest.TestCase):
    """L'ancienne recommandation est rendue, avec un seul graphe choisi par incident."""

    @classmethod
    def setUpClass(cls):
        import app
        cls.pid = "pt-susde-26nov2026"
        cls.other_pid = "pt-wsteth-30dec2027"  # une autre des 5 positions du site
        cls.guard = patch("app.ensure_live_data")
        cls.guard.start()
        for pid in (cls.pid, cls.other_pid):
            app._graphs[pid] = json.loads((ROOT / "data" / "graphs" / f"{pid}.json").read_text())
            app._reports[pid] = json.loads((ROOT / "data" / "reports" / f"{pid}.json").read_text())
            app._sims[pid] = json.loads((ROOT / "data" / "sim" / f"{pid}.json").read_text())

    @classmethod
    def tearDownClass(cls):
        import app
        cls.guard.stop()
        for pid in (cls.pid, cls.other_pid):
            app._graphs.pop(pid, None)
            app._reports.pop(pid, None)
            app._sims.pop(pid, None)

    def test_compatible_failure_shows_one_advanced_graph_and_parameters(self):
        import app
        from global_risk.current import render_current
        asset = next(n["id"] for n in app.graph_for(self.pid)["nodes"] if n["kind"] == "asset")
        page = render_current("pos=" + self.pid + "&incident=" + asset, self.pid, app)
        self.assertEqual(page.count('class="dep-graph"'), 1)
        self.assertNotIn('class="tabbar"', page)
        self.assertIn('class="sim-form"', page)
        self.assertNotIn('<select name="incident"', page)  # le bloc se choisit en cliquant dans le graphe
        self.assertIn('type="hidden" name="incident"', page)
        self.assertIn("DÉFAILLANCE SUPPOSÉE", page)
        self.assertIn("Analyse globale des risques", page)

    def test_noncompatible_failure_shows_one_simple_graph(self):
        import app
        from global_risk.current import render_current
        # Ethereum n'a pas de scénario (un arrêt de chaîne ne change aucun prix) : graphe simple
        chain = next(n["id"] for n in app.graph_for(self.pid)["nodes"] if n["kind"] == "chain")
        page = render_current("pos=" + self.pid + "&incident=" + chain, self.pid, app)
        self.assertEqual(page.count('class="dep-graph"'), 1)
        self.assertNotIn('class="sim-form"', page)
        self.assertNotIn('class="tabbar"', page)
        self.assertIn("Dépendances de la défaillance choisie", page)

    def test_other_position_also_gets_the_simulator(self):
        # Le simulateur est ouvert aux 11 positions : un actif d'une autre position donne aussi le graphe approfondi.
        import app
        from global_risk.current import render_current
        asset = next(n["id"] for n in app.graph_for(self.other_pid)["nodes"] if n["kind"] == "asset")
        page = render_current("pos=" + self.other_pid + "&incident=" + asset, self.other_pid, app)
        self.assertEqual(page.count('class="dep-graph"'), 1)
        self.assertIn('class="sim-form"', page)

    def test_clicking_advanced_graph_node_changes_selected_failure(self):
        import app
        from global_risk.current import _normalized_query
        graph = app.graph_for(self.pid)
        assets = [n["id"] for n in graph["nodes"] if n["kind"] == "asset"]
        query, advanced = _normalized_query(
            "pos=" + self.pid + "&incident=" + assets[0] + "&snode=" + assets[1] + "&run=1",
            self.pid, graph, app.report_for(self.pid), app)
        self.assertTrue(advanced)
        self.assertEqual(parse_qs(query)["incident"], [assets[1]])
        self.assertNotIn("run", parse_qs(query))

    def test_amount_survives_both_pages_and_advanced_controls(self):
        import app
        from global_risk.current import render_current
        asset = next(n["id"] for n in app.graph_for(self.pid)["nodes"] if n["kind"] == "asset")
        page = render_current("pos=" + self.pid + "&incident=" + asset + "&amount=10000000", self.pid, app)
        self.assertIn('name="amount"', page)
        self.assertIn('name="amount" value="10000000"', page)
        self.assertIn("/global?pos=" + self.pid + "&amp;amount=10000000", page)
        result, graph, report, context = app.live_analysis(
            self.pid, query="pos=" + self.pid + "&amount=10000000")
        self.assertEqual(result["position_size_usd"], 10000000)
        global_page = web.render_global(result, graph, report, context, app.available_positions())
        self.assertIn('name="amount"', global_page)
        self.assertIn('value="10000000"', global_page)
        self.assertIn("/?pos=" + self.pid + "&amp;amount=10000000", global_page)


if __name__ == "__main__":
    unittest.main()
