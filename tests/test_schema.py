import unittest

from schema import coverage, depths, edge, evidence, find_cycle, node, validate

EV = [evidence("test", "https://example.org", "2026-10-10T00:00:00Z")]


def small_graph():
    nodes = [
        node("pos", "PT", "position"),
        node("sy", "SY", "protocol"),
        node("susde", "sUSDe", "asset"),
        node("eth", "Ethereum", "chain"),
        node("morpho", "Morpho PT/USDC", "lending"),
    ]
    edges = [
        edge("sy", "pos", "backing", "verified_onchain", EV),
        edge("susde", "sy", "backing", "verified_onchain", EV),
        edge("eth", "pos", "settlement", "documented", EV),
        edge("morpho", "pos", "collateral_liquidation", "api", EV, {"market_id": "0xabc"}),
    ]
    return {"position": {"id": "pos"}, "nodes": nodes, "edges": edges}


class SchemaTests(unittest.TestCase):
    def test_valid_graph_has_no_errors(self):
        self.assertEqual(validate(small_graph()), [])

    def test_missing_evidence_is_rejected(self):
        g = small_graph()
        g["edges"][0]["evidence"] = []
        self.assertTrue(any("preuve" in e for e in validate(g)))

    def test_lending_edge_needs_market_id(self):
        g = small_graph()
        g["edges"][3]["details"] = {}
        self.assertTrue(any("marché réel" in e for e in validate(g)))

    def test_cycle_is_detected(self):
        g = small_graph()
        g["edges"].append(edge("sy", "susde", "backing", "api", EV))
        self.assertIsNotNone(find_cycle(g))
        self.assertTrue(any("cycle" in e for e in validate(g)))

    def test_orphan_node_is_detected(self):
        g = small_graph()
        g["nodes"].append(node("lost", "Isolé", "oracle"))
        self.assertTrue(any("n'atteint pas" in e for e in validate(g)))

    def test_unknown_mechanism_is_rejected(self):
        g = small_graph()
        g["edges"][0]["mechanism"] = "magie"
        self.assertTrue(any("mécanisme" in e for e in validate(g)))

    def test_depth_and_coverage(self):
        g = small_graph()
        self.assertEqual(depths(g)["susde"], 2)
        cov = coverage(g)
        self.assertEqual(cov["dependencies"], 4)
        self.assertEqual(cov["max_depth"], 2)
        self.assertEqual(cov["by_status"]["verified_onchain"], 2)
        self.assertAlmostEqual(cov["verified_share"], 1.0)


if __name__ == "__main__":
    unittest.main()
