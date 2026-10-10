import unittest
from pathlib import Path

from risk import assess_graph, impact_factor, load_graph

GRAPH = load_graph(Path(__file__).resolve().parent.parent / "data" / "graph.json")


class GraphRiskTests(unittest.TestCase):
    def test_impact_multiplies_weights_along_the_path(self):
        self.assertAlmostEqual(impact_factor(GRAPH, "Oracle"), 0.8 * 0.5)

    def test_direct_dependency_passes_all_damage(self):
        self.assertAlmostEqual(impact_factor(GRAPH, "Ethena"), 1.0)

    def test_break_even_matches_formula(self):
        result = assess_graph(GRAPH, "Oracle", 40, 11)
        self.assertAlmostEqual(result["break_even_probability_percent"], 11 / (0.5 * 0.4))

    def test_cheap_exit_means_leave(self):
        self.assertEqual(assess_graph(GRAPH, "Oracle", 40, 1)["verdict"], "SORTIR")

    def test_position_cannot_be_the_incident(self):
        with self.assertRaises(ValueError):
            assess_graph(GRAPH, "PT-sUSDe", 40, 11)


if __name__ == "__main__":
    unittest.main()
