"""Le site doit refaire ses relevés et refuser toute réponse périmée."""

import unittest
import urllib.error
from unittest.mock import patch

import app
import pipeline
from risk_engine import sources as S


class LiveCollectionTests(unittest.TestCase):
    PID = "pt-susde-26nov2026"

    def setUp(self):
        app._last_refresh.clear()
        app._graphs.clear()
        app._reports.clear()
        app._sims.clear()

    def tearDown(self):
        app._last_refresh.clear()

    def test_recollects_after_five_minutes_or_manual_refresh(self):
        with patch.object(app.discover, "discover", return_value={}) as collect, \
             patch.object(app.pipeline, "build_report", return_value={}), \
             patch.object(app.SIM, "build_context", return_value={}), \
             patch.object(app.time, "monotonic", return_value=100.0):
            app.ensure_live_data(self.PID)
            app.ensure_live_data(self.PID)
            self.assertEqual(collect.call_count, 1)
            app.ensure_live_data(self.PID, force=True)
            self.assertEqual(collect.call_count, 2)
        with patch.object(app.discover, "discover", return_value={}) as collect, \
             patch.object(app.pipeline, "build_report", return_value={}), \
             patch.object(app.SIM, "build_context", return_value={}), \
             patch.object(app.time, "monotonic", return_value=401.0):
            app.ensure_live_data(self.PID)
            collect.assert_called_once_with(self.PID)

    def test_failed_refresh_cannot_serve_old_report(self):
        app._reports[self.PID] = {"stale": True}
        with patch.object(app.discover, "discover", side_effect=ConnectionError("API indisponible")):
            with self.assertRaisesRegex(ConnectionError, "API indisponible"):
                app.ensure_live_data(self.PID)
            self.assertEqual(app._reports, {})
            self.assertEqual(app._last_refresh, {})
            with self.assertRaisesRegex(ConnectionError, "API indisponible"):
                app.render_page("")

    def test_pendle_access_error_is_not_treated_as_low_liquidity(self):
        S.reset_sources()
        denied = urllib.error.HTTPError("https://example.org", 403, "Forbidden", {}, None)
        with patch.object(S, "_get_json", side_effect=denied):
            with self.assertRaises(urllib.error.HTTPError):
                S.pendle_quote(1, "0x" + "1" * 40, "0x" + "2" * 40, 1000)
        denied.close()

    def test_removed_offline_cli_option_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Option inconnue"):
            pipeline.main(["--offline"])


if __name__ == "__main__":
    unittest.main()
