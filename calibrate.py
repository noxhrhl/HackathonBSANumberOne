"""Taux de base des défaillances, calculés sur la base d'incidents de DeFiLlama.

Méthode (explicable en 30 secondes) :
  1. On prend les exploits ≥ 5 M$ entre le 1er janvier 2021 et le 1er janvier 2026 (5 ans).
  2. On les relie à la catégorie DeFiLlama du protocole touché (Lending, Dexs, Yield…).
  3. Taux annuel = nombre d'exploits / (nombre de protocoles de la catégorie × 5 ans).
     Deux groupes : « tous » (TVL ≥ 10 M$ aujourd'hui) et « gros » (TVL ≥ 500 M$).
  4. Borne haute à 95 % (loi de Poisson) : avec 0 incident, c'est la « règle de trois » 3/exposition.

Limite connue : biais du survivant (on compte les protocoles encore vivants aujourd'hui).
Le résultat est enregistré dans data/calibration/hacks_rates.json et lu par risk_engine/rules.py.

Usage (avec Internet) : python calibrate.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "calibration" / "hacks_rates.json"
CATEGORIES = ["Lending", "Dexs", "CDP", "Liquid Staking", "Yield", "Basis Trading", "Derivatives",
              "Bridge", "CEX", "Yield Aggregator", "RWA", "Liquid Restaking"]
GROUPS = {"all": 1e7, "large": 5e8}
WINDOW = (datetime(2021, 1, 1, tzinfo=timezone.utc).timestamp(), datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
YEARS = 5
MIN_AMOUNT = 5e6

# Borne haute unilatérale à 95 % d'une loi de Poisson pour k événements observés
# (= quantile χ²(0,95 ; 2k+2) / 2). k = 0 donne la « règle de trois ».
POISSON_UPPER_95 = [2.996, 4.744, 6.296, 7.754, 9.154, 10.513, 11.842, 13.148, 14.435, 15.705, 16.962]


def poisson_upper(k: int) -> float:
    if k < len(POISSON_UPPER_95):
        return POISSON_UPPER_95[k]
    return k + 1.645 * k ** 0.5 + 1.0  # approximation normale au-delà


def rates(hacks: list, protocols: list) -> dict:
    by_id = {str(p["id"]): p for p in protocols}
    window = [h for h in hacks if WINDOW[0] <= h["date"] < WINDOW[1] and (h.get("amount") or 0) >= MIN_AMOUNT
              and h.get("defillamaId")]
    out = {}
    for cat in CATEGORIES:
        out[cat] = {}
        for group, min_tvl in GROUPS.items():
            pool = {str(p["id"]) for p in protocols if p.get("category") == cat and (p.get("tvl") or 0) >= min_tvl}
            hits = [h for h in window if str(h["defillamaId"]) in pool]
            exposure = len(pool) * YEARS
            out[cat][group] = {
                "protocols": len(pool), "hacks": len(hits),
                "rate": len(hits) / exposure if exposure else None,
                "upper95": poisson_upper(len(hits)) / exposure if exposure else None,
                "events": [f"{datetime.fromtimestamp(h['date'], timezone.utc):%Y-%m-%d} {h['name']} "
                           f"{h['amount'] / 1e6:.1f} M$ ({h.get('classification')})" for h in hits][:8],
            }
    oracle = [h for h in hacks if WINDOW[0] <= h["date"] < WINDOW[1] and (h.get("amount") or 0) >= MIN_AMOUNT
              and h.get("classification") == "Oracle Manipulation"]
    out["_oracle_manipulation_all"] = {"hacks": len(oracle)}
    _ = by_id
    return out


def main():
    from risk_engine import sources as S
    hacks = S._get_json(f"{S.LLAMA}/hacks", 90)
    protocols = S._get_json(f"{S.LLAMA}/protocols", 90)
    result = {"method": __doc__.split("Usage")[0].strip(), "source": f"{S.LLAMA}/hacks + {S.LLAMA}/protocols",
              "fetched_at": S.now_iso(), "window": ["2021-01-01", "2026-01-01"], "min_amount_usd": MIN_AMOUNT,
              "categories": rates(hacks, protocols)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print("écrit", OUT)


if __name__ == "__main__":
    main()
