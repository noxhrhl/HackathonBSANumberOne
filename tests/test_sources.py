"""La collecte en ligne ne doit jamais masquer un échec avec une ancienne réponse."""

import unittest

from risk_engine import sources as S


class OnlineSourceTests(unittest.TestCase):
    def setUp(self):
        S.reset_sources()

    def test_reuses_response_only_within_current_collection(self):
        calls = []

        def fetch():
            calls.append(1)
            return {"value": len(calls)}

        self.assertEqual(S.cached("example", "key", "https://example.org", fetch), {"value": 1})
        self.assertEqual(S.cached("example", "key", "https://example.org", fetch), {"value": 1})
        S.reset_sources()
        self.assertEqual(S.cached("example", "key", "https://example.org", fetch), {"value": 2})

    def test_network_failure_has_no_fallback(self):
        S.cached("example", "key", "https://example.org", lambda: {"old": True})
        S.reset_sources()

        def failed():
            raise ConnectionError("source indisponible")

        with self.assertRaisesRegex(ConnectionError, "source indisponible"):
            S.cached("example", "key", "https://example.org", failed)


if __name__ == "__main__":
    unittest.main()
