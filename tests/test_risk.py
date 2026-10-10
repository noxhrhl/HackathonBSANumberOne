import unittest

from risk_engine.risk import assess


class RiskTests(unittest.TestCase):
    def test_break_even_uses_the_same_position_value(self):
        result = assess(10_000, 20, 3)
        self.assertEqual(result["scenario_loss_usd"], 2_000)
        self.assertEqual(result["exit_cost_usd"], 300)
        self.assertEqual(result["break_even_probability_percent"], 15)

    def test_no_scenario_loss_has_no_break_even_probability(self):
        self.assertIsNone(assess(10_000, 0, 3)["break_even_probability_percent"])

    def test_invalid_percentage_is_rejected(self):
        with self.assertRaises(ValueError):
            assess(10_000, 120, 3)


if __name__ == "__main__":
    unittest.main()
