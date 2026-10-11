"""Règles de risque : d'où vient chaque nombre (détails et sources dans NOTES.md).

Trois familles de paramètres, chacune avec une fourchette BAS / BASE / HAUT :

1. lam (λ) — fréquence annuelle de défaillance d'un nœud « en temps normal ».
   Tirée de la base d'exploits DeFiLlama (data/calibration/hacks_rates.json) quand une
   catégorie correspond, sinon d'incidents historiques documentés.
   Probabilité sur l'horizon H (en jours) : p = 1 − exp(−λ · H / 365).

2. sev — perte de valeur du nœud SI il défaille (fraction entre 0 et 1), tirée
   d'incidents réels (USDC 2023 −12 %, stETH 2022 −6 %, Stream xUSD 2025 −77 %, Mango 2022…).

3. w — part du dommage qui traverse une arête, selon son MÉCANISME. Certaines arêtes ont
   un w mesuré dans les données (part d'Ethena sur une bourse, part d'un collatéral dans Aave).

Ces paramètres alimentent la page « Analyse globale des risques ». La page principale n'utilise
aucune probabilité : elle passe par le simulateur de défaillance (risk_engine/simulate.py).
"""

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_CAL = json.loads((ROOT / "data" / "calibration" / "hacks_rates.json").read_text(encoding="utf-8"))["categories"]

SCENARIOS = ("low", "base", "high")


def _cal(cat, group="all", field="rate"):
    return _CAL[cat][group][field]


# ---------------------------------------------------------------------------
# 1 + 2. Règles par type de nœud — voir NOTES.md § « Nœuds »
# ---------------------------------------------------------------------------
NODE_RULES = {
    # Chaîne : PoS Ethereum depuis sept. 2022, un incident de finalité (mai 2023), jamais d'arrêt
    # de production de blocs. Un arrêt bloque les actions mais ne détruit pas la valeur. NOTES.md#chain
    "chain": {"lam": (0.10, 0.25, 0.75), "sev": (0.0, 0.02, 0.10)},
    # Actif / enveloppe (sUSDe, wstETH…) : catégorie « Liquid Staking » DeFiLlama, 1 exploit sur
    # 355 protocole-années ; borne haute = règle de trois sur les gros (3/70). NOTES.md#asset
    "asset": {"lam": (0.001, _cal("Liquid Staking"), _cal("Liquid Staking", "large", "upper95")),
              "sev": (0.06, 0.20, 0.70)},
    # Émetteur de dollar synthétique / stablecoin : 2 décrochages > 10 % parmi ~10 grands stablecoins
    # sur 2021-2025 (UST 2022, USDC 2023) = 2 / 50 ≈ 4 %/an (HAUT) ; « Basis Trading » DeFiLlama :
    # 0 exploit sur 95 protocole-années (borne 3,2 %). BASE = 1,5 %. NOTES.md#issuer
    "issuer": {"lam": (0.005, 0.015, 0.04), "sev": (0.03, 0.20, 0.77)},
    # Oracle : 27 manipulations d'oracle ≥ 5 M$ en 5 ans, surtout sur des flux peu liquides.
    # La qualité du flux (A5) choisit la borne : voir oracle_adjust(). NOTES.md#oracle
    "oracle": {"lam": (0.003, 0.010, 27 / (62 * 5)), "sev": (0.05, 0.30, 1.0)},
    # AMM / DEX : « Dexs » 5 exploits / 415 protocole-années (Curve 2023, Balancer 2025). NOTES.md#venue
    "venue": {"lam": (0.005, _cal("Dexs"), _cal("Dexs", "all", "upper95")), "sev": (0.05, 0.30, 0.80)},
    # Prêt : « Lending » 3 / 310 ; gros prêteurs 0 / 60 → borne haute 5 %. Euler 2023 : 197 M$. NOTES.md#lending
    "lending": {"lam": (0.003, _cal("Lending"), _cal("Lending", "large", "upper95")), "sev": (0.02, 0.15, 0.60)},
    # Contrats d'un protocole (SY de Pendle) : « Yield » 1 / 145. Penpie 2024 : 27 M$ (pas Pendle). NOTES.md#protocol
    "protocol": {"lam": (0.002, _cal("Yield"), _cal("Yield", "all", "upper95")), "sev": (0.10, 0.50, 1.0)},
    # Bourse centralisée : gros CEX 4 / 120 (Bybit 2025 1,4 Md$ remboursés ; FTX 2022). NOTES.md#perp
    "perp_exchange": {"lam": (0.01, _cal("CEX", "large"), _cal("CEX", "large", "upper95")), "sev": (0.0, 0.25, 1.0)},
    # Dépositaire : HYPOTHÈSE (peu d'historique ; SVB 2023 pour les banques de Circle). NOTES.md#custodian
    "custodian": {"lam": (0.002, 0.005, 0.02), "sev": (0.05, 0.30, 1.0), "assumption": True},
    # Gouvernance / clés d'administration : HYPOTHÈSE (attaques de gouvernance rares). NOTES.md#admin
    "admin": {"lam": (0.002, 0.005, 0.02), "sev": (0.05, 0.30, 1.0), "assumption": True},
    # Pont : « Bridge » 6 / 260 (Wormhole 2022, Multichain 2023). NOTES.md#bridge
    "bridge": {"lam": (0.005, _cal("Bridge"), _cal("Bridge", "all", "upper95")), "sev": (0.10, 0.50, 1.0)},
}
DEFAULT_RULE = {"lam": (0.005, 0.01, 0.05), "sev": (0.05, 0.30, 1.0), "assumption": True}

# ---------------------------------------------------------------------------
# 3. Poids par mécanisme — voir NOTES.md § « Mécanismes »
# ---------------------------------------------------------------------------
MECHANISM_W = {
    # Valeur du sous-jacent : transmise presque entièrement (sUSDe −x % ⇒ PT −x % à l'échéance).
    "backing": (0.9, 1.0, 1.0),
    # Prix faux → mauvaises liquidations. USDe 10/2025 : décrochage limité à Binance (w bas) ;
    # Mango 10/2022 : prix manipulé = protocole vidé (w haut). NOTES.md#pricing
    "pricing": (0.05, 0.30, 0.80),
    # AMM dégradé : pour un PT gardé à l'échéance, la perte n'est que temporaire (valeur de marché).
    "exit_liquidity": (0.0, 0.10, 0.30),
    # Ventes forcées par liquidations : stETH 06/2022, décote ~6 % sous la pression vendeuse.
    "collateral_liquidation": (0.02, 0.10, 0.30),
    # Arrêt de chaîne : on ne peut plus agir ; Solana 02/2024 (5 h) n'a pas détruit la valeur.
    "settlement": (0.0, 0.05, 0.20),
    # Couverture sur une bourse : w = part du collatéral sur la bourse × perte en cas de défaut.
    # Avec garde hors-bourse (Copper/Ceffu), on perd surtout le PnL non réglé. NOTES.md#hedging
    "hedging": (0.05, 0.30, 1.0),
    # Garde : part détenue inconnue (Ethena ne la publie pas) → HYPOTHÈSE. NOTES.md#custody
    "custody": (0.05, 0.25, 0.50),
    # Créance douteuse : w = part du collatéral × fraction non couverte par les liquidations.
    "bad_debt": (0.10, 0.30, 0.70),
}
# Mécanismes dont le w est « part mesurée × facteur » (la part vient des données)
SHARE_MECHANISMS = {"hedging": "share_of_backing", "bad_debt": "share_of_collateral", "backing": "share_of_backing"}

# Rôle d'un flux dans un oracle composé (NOTES.md#oracle-roles)
FEED_ROLE_W = {
    "primaire": (0.3, 0.5, 1.0),   # protégé par le secours après le délai de bascule
    "secours": (0.05, 0.2, 0.5),   # utilisé seulement après une contestation
    "combo": (0.9, 1.0, 1.0),      # flux multiplié dans le prix : son erreur passe entièrement
}

def horizon_p(lam: float, horizon_days: float) -> float:
    """p = 1 − exp(−λ·H/365) : probabilité d'au moins une défaillance sur l'horizon."""
    return 1.0 - math.exp(-lam * horizon_days / 365.0)


def oracle_adjust(node: dict) -> dict:
    """A5 — qualité du flux : choisit la borne de λ selon ce qui a été lu on-chain."""
    flags = set(node.get("flags", []))
    t = (node.get("details") or {}).get("type")
    lo, base, hi = NODE_RULES["oracle"]["lam"]
    if "non_market_price" in flags:
        return {"lam": (lo, lo, base), "why": "prix calculé (décote linéaire) : impossible à manipuler, mais aveugle à un dépeg"}
    if "undecoded" in flags:
        return {"lam": (base, hi, hi), "why": "oracle non décodé : borne haute par prudence"}
    if t == "meta_oracle":
        return {"lam": (lo, lo, base), "why": "méta-oracle avec secours : défaillance propre rare ; le risque passe par ses flux"}
    if t == "chainlink_proxy" and (node.get("metrics") or {}).get("operators", 0) >= 10:
        return {"lam": (lo, base, base * 3), "why": "flux Chainlink agrégé par de nombreux opérateurs"}
    if "single_source" in flags:
        return {"lam": (base, hi, hi), "why": "source unique de prix de marché : borne haute"}
    return {"lam": (lo, base, hi), "why": "règle générale des oracles"}


def node_params(node: dict, horizon_days: float, scenario: str = "base") -> dict:
    """p (horizon), sev, et l'explication, pour un nœud et un scénario BAS/BASE/HAUT."""
    i = SCENARIOS.index(scenario)
    rule = NODE_RULES.get(node["kind"], DEFAULT_RULE)
    lam, why = rule["lam"], None
    if node["kind"] == "oracle":
        adj = oracle_adjust(node)
        lam, why = adj["lam"], adj["why"]
    return {"lam": lam[i], "p": horizon_p(lam[i], horizon_days), "sev": rule["sev"][i],
            "lam_range": lam, "sev_range": rule["sev"], "assumption": bool(rule.get("assumption")),
            "why": why}


def edge_weight(edge: dict, scenario: str = "base") -> dict:
    """w d'une arête : règle du mécanisme, ajustée par les données mesurées sur l'arête."""
    i = SCENARIOS.index(scenario)
    mech = edge["mechanism"]
    d = edge.get("details") or {}
    rng = MECHANISM_W[mech]
    why = f"règle du mécanisme « {mech} »"
    role = d.get("role")
    if mech == "pricing" and role in ("primaire", "secours"):
        rng = FEED_ROLE_W[role]
        why = f"flux {role} d'un méta-oracle"
    elif mech == "pricing" and role and role.upper().startswith(("BASE_FEED", "QUOTE_FEED", "BASEFEED", "QUOTEFEED")):
        rng = FEED_ROLE_W["combo"]
        why = "flux combiné dans le prix de l'oracle"
    share_key = SHARE_MECHANISMS.get(mech)
    if share_key and d.get(share_key) is not None and mech != "backing":
        share = float(d[share_key])
        w = (share * rng[0], share * rng[1], share * rng[2])
        why = f"part mesurée {share:.1%} × facteur de perte {rng[i]:.0%}"
        rng = w
    elif mech == "backing" and d.get("share_of_backing") is not None:
        share = float(d["share_of_backing"])
        rng = (share * rng[0], share * rng[1], share * rng[2])
        why = f"part mesurée {share:.1%} du collatéral"
    if mech == "collateral_liquidation" and (d.get("pressure_ratio") or 0) > 1:
        rng = (rng[1], rng[2], rng[2])
        why = f"collatéral = {d['pressure_ratio']:.1f}× la liquidité de l'AMM : borne haute"
    return {"w": rng[i], "w_range": rng, "why": why}
