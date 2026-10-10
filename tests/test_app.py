"""Test de fumée : la page s'affiche pour chaque position, chaque composant et chaque option."""
import json
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
        app._models.clear()

    def test_default_page(self):
        import app
        html = app.render_page("")
        self.assertIn("LA RÉPONSE À 3 H DU MATIN", html)
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
                    self.assertIn("LA RÉPONSE", html)

    def test_all_options_render(self):
        import app
        for extra in ("regime=normal", "view=prudent", "prop=sum", "p=0", "p=100", "c1=0", "c1=50", "p=abc", "incident=inconnu"):
            with self.subTest(extra):
                self.assertIn("LA RÉPONSE", app.render_page(extra))


if __name__ == "__main__":
    unittest.main()
