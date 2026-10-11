"""Chaîne complète : découvrir → rapport → données du simulateur (relevé en ligne, lecture seule).

  python pipeline.py pt-susde-26nov2026          # une position
  python pipeline.py --all                       # les 5 positions du site

Écrit data/graphs/<id>.json (le graphe découvert), data/reports/<id>.json (le rapport) et
data/sim/<id>.json (données de base du simulateur de défaillance : prix, état des AMM, oracles).
La page web lit ces fichiers et recalcule à la volée quand on change l'incident ou p.
"""

import json
import sys
from pathlib import Path

from risk_engine import discover, model as M, simulate, sources as S

ROOT = Path(__file__).resolve().parent
GRAPHS = ROOT / "data" / "graphs"
REPORTS = ROOT / "data" / "reports"
SIM = ROOT / "data" / "sim"
QUOTE_FRACTIONS = (0.25, 0.5, 1.0, 1.5, 2.5)


def position_cfg(pid):
    return next(p for p in discover.POSITIONS["positions"] if p["id"] == pid)


# ---------------------------------------------------------------------------
# Coût de sortie
# ---------------------------------------------------------------------------

def exit_quotes_for(pid, graph=None):
    """Cotations réelles (hors crise) : coût total de vente pour plusieurs tailles."""
    pos = position_cfg(pid)
    out = {"type": pos["type"], "quotes": [], "capacity": 1.0, "source": None}
    if pos["type"] == "pendle_pt":
        m = discover.pendle_market_for_pt(pos["chain_id"], pos["pt"])
        under = S.strip_chain(m["underlyingAsset"])
        prices = S.pendle_prices(pos["chain_id"], [pos["pt"], under])
        pt_price = prices[pos["pt"].lower()]
        units = pos["size_usd"] / pt_price
        cap = None
        for f in QUOTE_FRACTIONS:
            q = S.pendle_quote(pos["chain_id"], pos["pt"], under, round(units * f))
            notional = round(units * f) * pt_price
            if q.get("ok"):
                cost = abs(float(q["priceImpact"])) + float(q["fee_usd"]) / notional
                out["quotes"].append({"fraction": f, "size_usd": notional, "cost": cost,
                                      "price_impact": float(q["priceImpact"]), "fee_usd": float(q["fee_usd"])})
            else:
                out["quotes"].append({"fraction": f, "size_usd": notional, "cost": None, "error": q.get("error", "")[:160]})
                cap = f if cap is None else cap
        ok = [q for q in out["quotes"] if q["cost"] is not None]
        out["capacity_fraction_normal"] = max((q["fraction"] for q in ok), default=0.0)
        out["refused_from_fraction"] = cap
        out["source"] = "API Pendle « convert » (cotation réelle, hors crise)"
    elif pos["type"] in ("aave_supply", "morpho_supply"):
        g = graph or json.loads((GRAPHS / f"{pid}.json").read_text(encoding="utf-8"))
        avail = g["position"]["info"].get("available_liquidity_usd") or 0.0
        out["available_liquidity_usd"] = avail
        out["capacity_fraction_normal"] = min(1.0, avail / pos["size_usd"]) if pos["size_usd"] else 1.0
        out["quotes"].append({"fraction": 1.0, "size_usd": pos["size_usd"], "cost": 0.0,
                              "note": "retrait sans glissement tant que la liquidité disponible suffit"})
        out["source"] = "liquidité disponible (API Aave / Morpho)"
    else:  # pas de méthode de cotation pour ce type : aucune cotation plutôt qu'un chiffre inventé
        out["source"] = "aucune cotation automatique pour ce type de position"
    return out


# ---------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------

def default_incident(graph):
    """Bloc présélectionné sur le site. PT : l'exemple de Belem, l'oracle du marché de prêt (couche 3).
    Autres positions : l'actif directement détenu, prêté ou mis en garantie (simulable : chute de prix)."""
    pos = graph["position"]["id"]
    if graph["position"].get("type") != "pendle_pt":
        nodes = {n["id"]: n for n in graph["nodes"]}
        direct = [e for e in graph["edges"] if e["to"] == pos and e["mechanism"] == "backing"
                  and nodes[e["from"]]["kind"] == "asset"]
        direct.sort(key=lambda e: (e.get("details") or {}).get("role") != "collateral")
        if direct:
            return direct[0]["from"]
    oracles = [n for n in graph["nodes"] if n["kind"] == "oracle"]
    top = [n for n in oracles if any(e["from"] == n["id"] and e["to"].startswith("lending:") for e in graph["edges"])]
    pick = (top or oracles or [n for n in graph["nodes"] if n["kind"] == "issuer"] or
            [n for n in graph["nodes"] if n["id"] != pos])[0]
    return pick["id"]


def build_report(graph):
    """Rapport léger d'une position : uniquement ce que lisent les pages du site.

    default_incident : bloc présélectionné (global_risk/current.py, app.parse) ;
    data_fetched_at : heure du relevé (badge, analyse globale) ; horizon_days et exit.quotes :
    analyse globale. L'ancien Monte Carlo et la décision « combien vendre » ont été retirés."""
    pid = graph["position_id"]
    return {
        "position_id": pid, "label": graph["position"]["label"], "size_usd": graph["position"]["size_usd"],
        "generated_at": S.now_iso(), "data_fetched_at": data_timestamp(graph),
        "horizon_days": M.horizon_days(graph), "coverage": graph["checks"]["coverage"],
        "errors": graph["checks"]["errors"], "loops": graph.get("loops", []),
        "default_incident": default_incident(graph), "exit": {"quotes": exit_quotes_for(pid, graph)},
    }


def data_timestamp(graph):
    ts = sorted(s["fetched_at"] for s in graph["meta"]["sources"] if s.get("fetched_at") not in (None, "?"))
    return {"oldest": ts[0] if ts else None, "newest": ts[-1] if ts else None}


def run(pid):
    g = discover.discover(pid)
    GRAPHS.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (GRAPHS / f"{pid}.json").write_text(json.dumps(g, indent=1, ensure_ascii=False), encoding="utf-8")
    rep = build_report(g)
    (REPORTS / f"{pid}.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    SIM.mkdir(parents=True, exist_ok=True)  # données de base du simulateur de défaillance (prix, AMM, oracles)
    (SIM / f"{pid}.json").write_text(json.dumps(simulate.build_context(g), indent=1, ensure_ascii=False), encoding="utf-8")
    return g, rep


def main(argv):
    unknown = [a for a in argv if a.startswith("--") and a != "--all"]
    if unknown:
        raise ValueError(f"Option inconnue : {', '.join(unknown)}")
    ids = [a for a in argv if not a.startswith("--")]
    if "--all" in argv or not ids:
        ids = [p["id"] for p in discover.POSITIONS["positions"]]
    S.reset_sources()
    for pid in ids:
        g, rep = run(pid)
        cov = rep["coverage"]
        print(f"{pid:24s} {cov['dependencies']:3d} dépendances, profondeur {cov['max_depth']}, "
              f"{cov['verified_share']:.0%} non supposées | bloc présélectionné {rep['default_incident'][:40]}"
              + (f" ERREURS {rep['errors']}" if rep["errors"] else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
