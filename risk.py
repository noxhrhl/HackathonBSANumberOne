"""Calcul minimal d'un scénario de risque, indépendant de l'interface."""

from math import isfinite


def assess(position_usd: float, loss_percent: float, exit_percent: float) -> dict:
    """Compare une perte conditionnelle et le coût d'une sortie immédiate.

    loss_percent est la perte *si* l'incident arrive, pas sa probabilité.
    Les pourcentages s'appliquent tous deux à la valeur actuelle de la position.
    """
    values = (position_usd, loss_percent, exit_percent)
    if not all(isfinite(value) for value in values):
        raise ValueError("Les valeurs doivent être des nombres finis.")
    if position_usd <= 0:
        raise ValueError("La valeur de la position doit être supérieure à zéro.")
    if not 0 <= loss_percent <= 100 or not 0 <= exit_percent <= 100:
        raise ValueError("Les pourcentages doivent être compris entre 0 et 100.")

    scenario_loss = position_usd * loss_percent / 100
    exit_cost = position_usd * exit_percent / 100
    break_even_probability = (
        100 * exit_cost / scenario_loss if scenario_loss > 0 else None
    )
    return {
        "scenario_loss_usd": scenario_loss,
        "exit_cost_usd": exit_cost,
        "break_even_probability_percent": break_even_probability,
    }
