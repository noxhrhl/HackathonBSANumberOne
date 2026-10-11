"""Test de fumée : la page s'affiche pour chaque position, chaque composant et chaque option."""
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless((ROOT / "data" / "reports" / "pt-susde-26nov2026.json").exists(),
                     "lancer d'abord : python pipeline.py --all --book")
class PageSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        cls.online_guard = patch("app.ensure_live_data")
        cls.online_guard.start()  # les fichiers générés servent ici de fixtures de rendu
        for p in app.available_positions():
            pid = p["id"]
            app._graphs[pid] = json.loads((ROOT / "data" / "graphs" / f"{pid}.json").read_text())
            app._reports[pid] = json.loads((ROOT / "data" / "reports" / f"{pid}.json").read_text())
            path = ROOT / "data" / "sim" / f"{pid}.json"
            app._sims[pid] = json.loads(path.read_text()) if path.exists() else None

    @classmethod
    def tearDownClass(cls):
        import app
        cls.online_guard.stop()
        app._graphs.clear()
        app._reports.clear()
        app._sims.clear()

    def test_default_page(self):
        import app
        html = app.render_page("")
        self.assertIn("DÉFAILLANCE SUPPOSÉE", html)
        # page sans probabilité : ni champ de probabilité, ni régime de sortie, ni « Combien vendre », ni « p · I »
        for gone in ('name="p"', 'name="c1"', 'name="regime"', 'name="view"', 'name="prop"', "02 / DÉCIDER", "LA RÉPONSE À 3 H"):
            self.assertNotIn(gone, html)
        self.assertNotRegex(html, r">[^<]*p \d+,\d+ % · I ")
        self.assertIn("<svg", html)
        self.assertIn("Données", html)

    def test_every_position_and_component_renders(self):
        import app
        for p in app.available_positions():
            g = app.graph_for(p["id"])
            kinds_done = set()
            for n in g["nodes"]:
                if n["kind"] in ("position",) or n["kind"] in kinds_done:
                    continue
                kinds_done.add(n["kind"])
                with self.subTest(pos=p["id"], node=n["id"]):
                    html = app.render_page(f"pos={p['id']}&incident={n['id']}&p=40")
                    self.assertIn("DÉFAILLANCE SUPPOSÉE", html)

    def test_site_shows_five_positions_and_click_selection(self):
        import app
        self.assertEqual([p["id"] for p in app.available_positions()],
                         ["pt-susde-26nov2026", "pt-wsteth-30dec2027", "aave-v3-usdc", "morpho-ptsusde-usdc",
                          "morpho-ptsusde-borrow"])
        html = app.render_page("pos=pt-susde-26nov2026&incident=chain:ethereum")
        self.assertNotIn('<select name="incident"', html)
        self.assertIn("cliquez sur un bloc du graphe", html)
        links = [h for h in re.findall(r'href="(/\?[^"]+)"', html) if "snode=issuer%3Aethena" in h]
        self.assertTrue(links and "incident=issuer%3Aethena" in links[0])  # chaque bloc est un lien de sélection
        self.assertIn("ne change aucun prix", html)                                 # Ethereum : pas de chiffre inventé

    def test_all_options_render(self):
        import app
        for extra in ("regime=normal", "view=prudent", "prop=sum", "p=0", "p=100", "c1=0", "c1=50", "p=abc", "incident=inconnu"):
            with self.subTest(extra):
                self.assertIn("DÉFAILLANCE SUPPOSÉE", app.render_page(extra))  # anciens paramètres ignorés


    def test_stay_or_exit_on_top_and_curve_below(self):
        import app
        g = app.graph_for("morpho-ptsusde-borrow")
        oid = next(n["id"] for n in g["nodes"] if n["id"].startswith("oracle:1:0xf8b29e7e9c00ce53143"))
        html = app.render_page(f"pos=morpho-ptsusde-borrow&incident={oid}&snode={oid}")
        top, bottom = html.find('class="verdict'), html.find('id="decision"')
        self.assertTrue(0 < top < html.find("DÉFAILLANCE SUPPOSÉE") < bottom)   # réponse en haut, détail en bas
        self.assertIn("VENDRE 20 %", html)
        self.assertIn('class="curve"', html)
        # bloc sans scénario chiffré : on le dit, pas de chiffre inventé
        html = app.render_page("pos=pt-susde-26nov2026&incident=chain:ethereum")
        self.assertIn("Pas de recommandation chiffrée", html)
        self.assertNotIn('id="decision"', html)


if __name__ == "__main__":
    unittest.main()
