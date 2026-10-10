"""Simulation d'impact : SI ce composant défaille, que devient la position ? (aucune probabilité).

Un seul moteur, générique, pour toutes les défaillances :

  1. le scénario modifie UNE variable d'état du nœud choisi
       price_shock      → prix de marché d'un actif        (m  = 1 − sévérité)
       oracle_failure   → prix rapporté par un oracle      (om = 1 − sévérité), marché inchangé
       liquidity_crisis → réserves d'un pool AMM           (lm = 1 − sévérité)
  2. on parcourt le graphe en aval, dans l'ordre topologique ;
  3. chaque nœud RECALCULE son état à partir de l'état de TOUS ses parents (pas d'addition de
     pertes : un nœud atteint par deux chemins n'est compté qu'une fois) ;
  4. chaque arête traversée est rattachée à un modèle de transmission (MODELS) : type de
     dépendance, mécanisme, formule, paramètres, source, hypothèses ;
  5. la position applique les règles de son protocole (valeur de marché, oracle, LTV, facteur
     de santé, liquidation Morpho, sortie sur l'AMM Pendle) ;
  6. on calcule les métriques de sévérité (marché, liquidation, exécution, total).

Variables d'état séparées : prix de marché (m), prix d'oracle (om), liquidité de pool (lm),
prix de sortie exécutable, valeur du collatéral, valeur de la dette, statut de liquidation,
fonds propres. Un mécanisme non modélisable est marqué INCONNU et propagé comme tel :
les chiffres affichés n'incluent jamais une transmission inventée.

Ajouter une défaillance : une entrée dans SCENARIOS. Ajouter un protocole : un modèle dans
MODELS, une règle dans classify(), un évaluateur de nœud ou de position.
"""

import math
import re
from datetime import datetime

from . import amm
from . import sources as S

MORPHO_MAX_LIF = 1.15        # Morpho Blue, ConstantsLib.MAX_LIQUIDATION_INCENTIVE_FACTOR
MORPHO_LIQ_CURSOR = 0.3      # Morpho Blue, ConstantsLib.LIQUIDATION_CURSOR
USD_STABLE_ASSUMED = {"USDC", "USDT", "PYUSD", "DAI", "USDS"}
DEFAULT_HOURS = 24

SCENARIOS = {
    "price_shock": {"label": "Chute de prix / dépeg", "kinds": ("asset",), "default": 0.05, "max": 0.90,
                    "variable": "prix de marché", "help": "Baisse du prix de marché de l'actif (ex. USDe −5 %, stETH −20 %)."},
    "oracle_failure": {"label": "Défaillance d'oracle", "kinds": ("oracle",), "default": 0.10, "max": 0.90,
                       "variable": "prix rapporté", "help": "L'oracle rapporte un prix trop bas ; le prix de marché ne bouge pas."},
    "liquidity_crisis": {"label": "Crise de liquidité", "kinds": ("venue",), "default": 0.80, "max": 0.99,
                         "variable": "liquidité du pool", "help": "Les LP retirent une part de la liquidité du pool (retrait proportionnel)."},
}

# ---------------------------------------------------------------------------
# Modèles de transmission (un par type d'arête)
# ---------------------------------------------------------------------------

MODELS = {
    "wrapper_rate": {
        "dependency": "Enveloppe ERC-4626 (ou wstETH)", "transmission": "prix de marché",
        "formula": "P_enveloppe = P_base × taux de conversion ; le taux (convertToAssets / stEthPerToken) ne dépend pas du prix de marché",
        "params": ["taux de conversion (on-chain)"], "source": "lien lu on-chain (asset() / stETH())",
        "assumptions": ["Le prix de marché de l'enveloppe suit sa valeur de rachat (pas de décote propre)"], "quantified": True},
    "sy_wrap": {
        "dependency": "Contrat SY Pendle", "transmission": "prix de marché",
        "formula": "1 SY = 1 jeton sous-jacent (SY.yieldToken) : P_SY = P_sous-jacent",
        "params": [], "source": "lien lu on-chain (yieldToken())", "assumptions": [], "quantified": True},
    "pt_backing": {
        "dependency": "PT Pendle adossé au SY", "transmission": "prix de marché",
        "formula": "P_PT = P_actif comptable × exp(−lnImpliedRate × t/an) ; à l'échéance 1 PT = 1 actif comptable",
        "params": ["lastLnImpliedRate (readState)", "échéance"], "source": "AMM Pendle (readState on-chain)",
        "assumptions": ["Taux implicite de l'AMM inchangé pendant l'incident (en crise réelle il monte souvent : perte supplémentaire non comptée)"],
        "quantified": True},
    "position_holds": {
        "dependency": "Position = l'actif lui-même", "transmission": "prix de marché",
        "formula": "Valeur = quantité × P_actif", "params": ["taille de la position"], "source": "data/positions.json",
        "assumptions": [], "quantified": True},
    "position_collateral": {
        "dependency": "Collatéral de la position", "transmission": "valeur du collatéral",
        "formula": "Valeur du collatéral (marché) = Q × P_marché ; (oracle) = Q × P_oracle",
        "params": ["Q (unités de collatéral)"], "source": "data/positions.json + prix API Pendle", "assumptions": [], "quantified": True},
    "position_debt": {
        "dependency": "Dette de la position", "transmission": "valeur de la dette",
        "formula": "Valeur de la dette (USD) = D × P_actif emprunté ; Morpho compte la dette en unités de l'actif emprunté",
        "params": ["D (unités empruntées)"], "source": "data/positions.json", "assumptions": [], "quantified": True},
    "position_loan_asset": {
        "dependency": "Actif prêté", "transmission": "prix de marché",
        "formula": "Valeur du dépôt = unités × P_actif", "params": [], "source": "API Aave / Morpho", "assumptions": [], "quantified": True},
    "lender_claim": {
        "dependency": "Créance sur un marché de prêt", "transmission": "créance douteuse",
        "formula": "Perte prêteur ≈ part du dépôt × max(0, dette × P_dette − collatéral × P_collatéral)",
        "params": ["dépôts, emprunts, collatéral du marché (API)"], "source": "API Morpho / Aave",
        "assumptions": ["Emprunteurs homogènes (un emprunteur représentatif : la répartition réelle n'est pas publiée)",
                        "Collatéral récupéré à son prix de marché"], "quantified": True},
    "observes": {
        "dependency": "Flux de prix qui lit le marché", "transmission": "prix d'oracle",
        "formula": "Prix rapporté = prix de marché de l'actif de base / prix de l'actif de cotation",
        "params": ["description() lue on-chain"], "source": "description() du flux (on-chain)",
        "assumptions": ["Le flux se met à jour (écart > seuil de déviation ou délai de mise à jour écoulé)",
                        "Plafond CAPO d'Aave : ne limite que les hausses"], "quantified": True},
    "observes_unknown": {
        "dependency": "Flux de prix au mécanisme non identifié", "transmission": "prix d'oracle",
        "formula": "INCONNU : le fournisseur ou la formule du flux n'est pas décodé", "params": [], "source": "description()",
        "assumptions": [], "quantified": False},
    "feed_combination": {
        "dependency": "Oracle Morpho (MorphoChainlinkOracleV2)", "transmission": "prix d'oracle",
        "formula": "prix = SCALE × BASE_FEED_1 × BASE_FEED_2 × taux du coffre / (QUOTE_FEED_1 × QUOTE_FEED_2)",
        "params": ["rôles des flux (lus on-chain)"], "source": "BASE_FEED_x() / QUOTE_FEED_x() on-chain",
        "assumptions": ["Le taux du coffre ERC-4626 (s'il y en a un) ne dépend pas du prix de marché"], "quantified": True},
    "meta_source": {
        "dependency": "Méta-oracle primaire + secours (Steakhouse MetaOracleDeviationTimelock)", "transmission": "prix d'oracle",
        "formula": "Prix = primaire ; bascule sur le secours si |primaire − secours| / moyenne > seuil pendant le délai de contestation",
        "params": ["deviationThreshold", "challengeTimelockDuration", "healingTimelockDuration", "price() des deux sources"],
        "source": "lus on-chain ; règle : steakhouse.financial/docs/products/infrastructure/metaoracle",
        "assumptions": ["Quelqu'un appelle challenge() dès que l'écart dépasse le seuil (fonction publique)"], "quantified": True},
    "oracle_to_lending": {
        "dependency": "Oracle utilisé par un marché de prêt", "transmission": "valeur du collatéral vue par le protocole",
        "formula": "Collatéral (oracle) = Q × price() / 1e36 ; santé = Collatéral(oracle) × LLTV / dette",
        "params": ["price() on-chain", "LLTV"], "source": "API Morpho + price() on-chain", "assumptions": [], "quantified": True},
    "collateral_value": {
        "dependency": "Collatéral d'un marché de prêt", "transmission": "valeur de marché du collatéral",
        "formula": "Valeur récupérable du collatéral = collatéral × P_marché", "params": ["collatéral total (API)"],
        "source": "API Morpho / Aave", "assumptions": [], "quantified": True},
    "liquidation_rule": {
        "dependency": "Règle de liquidation Morpho Blue", "transmission": "statut de liquidation",
        "formula": "liquidable si dette > Q × P_oracle × LLTV ; saisie = dette remboursée × LIF / P_oracle ; "
                   "LIF = min(1,15 ; 1 / (1 − 0,3 × (1 − LLTV)))",
        "params": ["LLTV", "LIF", "price()"], "source": "code Morpho Blue (Morpho.sol, ConstantsLib.sol)",
        "assumptions": ["Liquidation simulée totale (Morpho n'a pas de facteur de clôture) : cas défavorable",
                        "Éligible ≠ liquidé : un liquidateur doit agir"], "quantified": True},
    "forced_sales": {
        "dependency": "Ventes forcées par les liquidations d'un marché", "transmission": "pression vendeuse",
        "formula": "Effet secondaire : collatéral saisi revendu sur le marché (estimation, NON incluse dans le total)",
        "params": ["collatéral et dette du marché (API)"], "source": "API Morpho",
        "assumptions": ["Répartition des emprunteurs inconnue : estimation par un emprunteur représentatif"], "quantified": False},
    "amm_exit": {
        "dependency": "Pool AMM où l'on vend la position", "transmission": "prix de sortie exécutable",
        "formula": "Courbe Pendle V2 (MarketMathCore) ; un retrait proportionnel laisse le prix spot inchangé et réduit la profondeur",
        "params": ["totalPt, totalSy, scalarRoot, lnFeeRateRoot, lastLnImpliedRate (readState on-chain)", "SY.exchangeRate()"],
        "source": "readState() on-chain, vérifié contre RouterStatic.swapExactPtForSyStatic",
        "assumptions": ["AMM seul : les ordres limites Pendle (non garantis en crise) sont ignorés → borne prudente"], "quantified": True},
    "issuer_backing": {
        "dependency": "Émetteur qui garantit l'actif", "transmission": "solvabilité",
        "formula": "INCONNU : la perte de l'émetteur ne se traduit pas mécaniquement en prix", "params": [],
        "source": "documentation", "assumptions": [], "quantified": False},
    "issuer_deployment": {
        "dependency": "Collatéral de l'émetteur placé dans un protocole", "transmission": "solvabilité",
        "formula": "INCONNU : la perte du protocole sur ce placement n'est pas modélisée", "params": ["part du collatéral (API Ethena)"],
        "source": "API Ethena", "assumptions": [], "quantified": False},
    "pool_bad_debt": {
        "dependency": "Collatéral d'un pool multi-actifs (Aave)", "transmission": "créance douteuse",
        "formula": "INCONNU : la créance douteuse d'Aave dépend de chaque emprunteur (non publié par l'API)", "params": [],
        "source": "API Aave", "assumptions": [], "quantified": False},
    "not_modeled": {
        "dependency": "Dépendance non modélisée pour ce scénario", "transmission": "—",
        "formula": "INCONNU", "params": [], "source": "—", "assumptions": [], "quantified": False},
}


def classify(edge, nodes, graph):
    """Choisit le modèle de transmission d'une arête."""
    mech, d = edge["mechanism"], edge.get("details") or {}
    src, dst = nodes[edge["from"]], nodes[edge["to"]]
    sk, dk = src["kind"], dst["kind"]
    ptype = graph["position"].get("type")
    if edge.get("derived"):
        return edge["derived"]
    if mech == "backing":
        if sk == "asset" and dk == "asset" and "enveloppe" in (d.get("why") or ""):
            return "wrapper_rate"
        if sk == "asset" and dst["id"].startswith("protocol:pendle-sy"):
            return "sy_wrap"
        if src["id"].startswith("protocol:pendle-sy"):
            return "pt_backing"
        if sk == "asset" and dk == "position":
            if d.get("role") == "collateral":
                return "position_collateral"
            if d.get("role") == "debt":
                return "position_debt"
            return "position_holds" if ptype == "hold" else "position_loan_asset"
        if sk == "issuer":
            return "issuer_backing"
        if sk == "lending" and dk == "position":
            return "lender_claim"
        if sk == "lending" and dk == "issuer":
            return "issuer_deployment"
    if mech == "pricing":
        if sk == "oracle" and dk == "oracle":
            return "meta_source" if d.get("role") in ("primaire", "secours") else "feed_combination"
        if sk == "oracle" and dk == "lending":
            return "oracle_to_lending"
    if mech == "exit_liquidity":
        return "amm_exit"
    if mech == "collateral_liquidation":
        return "liquidation_rule" if dk == "position" else "forced_sales"
    if mech == "bad_debt":
        return "collateral_value" if (d.get("share_of_collateral") or 0) >= 0.999 else "pool_bad_debt"
    return "not_modeled"


# ---------------------------------------------------------------------------
# Contexte : prix, AMM et oracles relevés en ligne pour cette simulation
# ---------------------------------------------------------------------------

def _ts(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def _ev(source, key, label, status):
    m = S.meta(source, key)
    return {"label": label, "source": source, "url": m.get("url", ""), "fetched_at": m.get("fetched_at", "?"), "status": status}


def _incoming(graph, nid):
    return [e for e in graph["edges"] if e["to"] == nid]


def pendle_links(graph):
    """Pour chaque AMM Pendle : le jeton vendu (PT), le SY et l'actif sous-jacent du SY."""
    out = {}
    for v in graph["nodes"]:
        if not v["id"].startswith("venue:pendle:"):
            continue
        target = next((e["to"] for e in graph["edges"] if e["from"] == v["id"] and e["mechanism"] == "exit_liquidity"), None)
        sy = next((e["from"] for e in _incoming(graph, target) if e["from"].startswith("protocol:pendle-sy")), None)
        under = next((e["from"] for e in _incoming(graph, sy)), None) if sy else None
        out[v["id"]] = {"market": v.get("address"), "pt_node": target, "sy_node": sy, "underlying_node": under}
    return out


def build_context(graph):
    """Relève en ligne les états nécessaires à la simulation, en lecture seule."""
    nodes = {n["id"]: n for n in graph["nodes"]}
    pos = graph["position"]
    ctx = {"position_id": graph["position_id"], "built_at": S.now_iso(), "prices_usd": {}, "pendle": {}, "oracles": {},
           "lending": {}, "position": {}, "evidence": [], "missing": []}

    def safe(label, fn, *args):
        try:
            return fn(*args)
        except Exception as exc:  # donnée absente : le modèle concerné sera INCONNU
            ctx["missing"].append(f"{label} : {type(exc).__name__} {str(exc)[:120]}")
            return None

    links = pendle_links(graph)
    for vid, lk in links.items():
        pt_addr = pos["pt"] if lk["pt_node"] == pos["id"] and pos.get("pt") else (nodes.get(lk["pt_node"]) or {}).get("address")
        under_addr = (nodes.get(lk["underlying_node"]) or {}).get("address")
        sy_addr = (nodes.get(lk["sy_node"]) or {}).get("address")
        if not (pt_addr and under_addr and sy_addr and lk["market"]):
            ctx["missing"].append(f"{vid} : liens PT / SY / sous-jacent incomplets")
            continue
        prices = safe("prix Pendle", S.pendle_prices, 1, [pt_addr, under_addr]) or {}
        pkey = "prices_1_" + "_".join(a.lower()[:10] for a in [pt_addr, under_addr])
        if prices:
            ctx["prices_usd"][lk["pt_node"]] = prices.get(pt_addr.lower())
            ctx["prices_usd"][lk["underlying_node"]] = prices.get(under_addr.lower())
            ctx["prices_usd"][lk["sy_node"]] = prices.get(under_addr.lower())  # 1 SY = 1 sous-jacent
            ctx["evidence"].append(_ev("pendle", pkey, "Prix PT et sous-jacent (API Pendle)", "api"))
        st = safe("readState Pendle", S.pendle_market_state, lk["market"])
        rate = safe("SY.exchangeRate", S.sy_exchange_rate, sy_addr)
        if st and rate:
            from .keccak import selector
            key = f"call_{lk['market'].lower()}_{selector('readState(address)')}{S._arg_address(S.PENDLE_ROUTER)}"
            ev = _ev("rpc", key, f"readState() de l'AMM {nodes[vid]['name']}", "verified_onchain")
            st.update(sy_rate=rate, snapshot_ts=_ts(ev["fetched_at"]) if ev["fetched_at"] != "?" else int(st["expiry_ts"]) - 86400,
                      pt_node=lk["pt_node"], sy_node=lk["sy_node"], underlying_node=lk["underlying_node"])
            ctx["pendle"][vid] = st
            ctx["evidence"].append(ev)
            ctx["evidence"].append(_ev("rpc", f"call_{sy_addr}_{selector('exchangeRate()')}", "SY.exchangeRate()", "verified_onchain"))

    for n in graph["nodes"]:
        if n["kind"] == "oracle" and (n.get("details") or {}).get("type") in ("meta_oracle", "morpho_chainlink_v2", "vault_rate_only"):
            raw = safe(f"price() {n['id']}", S.oracle_price_raw, n["address"])
            if raw:
                from .keccak import selector
                ctx["oracles"][n["id"]] = {"raw": raw}
                ctx["evidence"].append(_ev("rpc", f"call_{n['address']}_{selector('price()')}", f"price() de {n['name'][:40]}", "verified_onchain"))

    for n in graph["nodes"]:
        if n["kind"] != "lending" or (n.get("details") or {}).get("protocol") != "morpho-blue":
            continue
        mt = n.get("metrics") or {}
        lltv = mt.get("lltv")
        oracle = next((e["from"] for e in _incoming(graph, n["id"]) if e["mechanism"] == "pricing"), None)
        ctx["lending"][n["id"]] = {"protocol": "morpho-blue", "lltv": lltv, "oracle": oracle,
                                   "lif": morpho_lif(lltv) if lltv else None, "collateral_usd": mt.get("collateral_usd"),
                                   "borrow_usd": mt.get("borrow_usd"), "supply_usd": mt.get("supply_usd")}

    size = pos["size_usd"]
    ptype = pos.get("type")
    pc = {"type": ptype, "size_usd": size}
    if ptype == "pendle_pt":
        price = ctx["prices_usd"].get(pos["id"])
        pc.update(units=size / price if price else None, venue=(pos.get("info") or {}).get("venue"))
    elif ptype == "morpho_borrow":
        mid = (pos.get("info") or {}).get("market_id") or pos["market_id"]
        lid = f"lending:morpho:1:{mid.lower()}"
        mk = safe("marché Morpho", S.morpho_market, 1, mid)
        coll_node = next((e["from"] for e in _incoming(graph, pos["id"]) if (e.get("details") or {}).get("role") == "collateral"), None)
        debt_node = next((e["from"] for e in _incoming(graph, pos["id"]) if (e.get("details") or {}).get("role") == "debt"), None)
        price = ctx["prices_usd"].get(coll_node)
        pc.update(lending=lid, collateral_node=coll_node, debt_node=debt_node, debt_units=pos["debt_usd"],
                  collateral_units=size / price if price else None,
                  venue=next((v for v, lk in links.items() if lk["pt_node"] == coll_node), None))
        if mk:
            dc = safe("decimals collatéral", S.erc20_decimals, mk["collateralAsset"]["address"])
            dl = safe("decimals dette", S.erc20_decimals, mk["loanAsset"]["address"])
            if dc is not None and dl is not None and lid in ctx["lending"]:
                ctx["lending"][lid].update(collateral_decimals=dc, loan_decimals=dl)
    elif ptype in ("aave_supply", "morpho_supply"):
        pc.update(available_liquidity_usd=(pos.get("info") or {}).get("available_liquidity_usd"),
                  lending=next((e["from"] for e in _incoming(graph, pos["id"]) if e["mechanism"] == "backing"
                                and e["from"].startswith("lending:")), None))
    ctx["position"] = pc
    for nid, n in nodes.items():  # stablecoins : 1 $ par HYPOTHÈSE si aucun prix relevé
        if n["kind"] == "asset" and n["name"] in USD_STABLE_ASSUMED and nid not in ctx["prices_usd"]:
            ctx["prices_usd"][nid] = 1.0
            ctx.setdefault("assumed_prices", []).append(n["name"])
    return ctx


def morpho_lif(lltv):
    return min(MORPHO_MAX_LIF, 1 / (1 - MORPHO_LIQ_CURSOR * (1 - lltv)))


def oracle_price_loan_units(ctx, oracle_id, lending_id):
    """price() Morpho converti en unités de dette par unité de collatéral (ex. USDC par PT)."""
    o = ctx["oracles"].get(oracle_id)
    lc = ctx["lending"].get(lending_id) or {}
    if not o or lc.get("loan_decimals") is None:
        return None
    return o["raw"] / 10 ** (36 + lc["loan_decimals"] - lc["collateral_decimals"])


# ---------------------------------------------------------------------------
# Flux de prix : quel actif observent-ils ? (arêtes « dérivées », propres à la simulation)
# ---------------------------------------------------------------------------

_ALIASES = {"ETH": "WETH"}


def feed_relation(n):
    """[(symbole, exposant)] observé par un flux, None s'il ne lit pas le marché, 'unknown' si indécodable."""
    d, flags = n.get("details") or {}, n.get("flags") or []
    if "non_market_price" in flags:
        return None
    typ, desc = d.get("type"), (d.get("description") or "")
    if not desc:
        return "unknown" if typ not in ("meta_oracle", "morpho_chainlink_v2", "vault_rate_only") and n["kind"] == "oracle" \
            and "undecoded" in flags else None
    s = desc.replace("Capped ", "")
    s = s.split(" for ", 1)[-1]
    parts = [p.strip() for p in s.split("/") if p.strip()]
    if typ == "aave_capo_adapter" and d.get("ratio_provider") and parts and parts[-1] == "USD" and len(parts) >= 2:
        m = re.search(r"\((\w+)\)", parts[-2])
        return [(m.group(1) if m else parts[-2], 1)]
    if typ in ("chainlink_proxy", "aave_capo_adapter") and len(parts) == 2:
        rel = [(parts[0], 1)]
        if parts[1] != "USD":
            rel.append((parts[1], -1))
        return rel
    return "unknown"


def derived_edges(graph):
    """Arêtes actif → flux : le flux lit le prix de marché de l'actif (d'après description() on-chain)."""
    by_sym = {}
    for n in graph["nodes"]:
        if n["kind"] == "asset":
            by_sym.setdefault(n["name"].lower(), n["id"])
    out = []
    for n in graph["nodes"]:
        if n["kind"] != "oracle":
            continue
        rel = feed_relation(n)
        if rel is None:
            continue
        desc = ((n.get("details") or {}).get("description") or "")
        if rel == "unknown":
            for sym, aid in by_sym.items():
                if re.search(rf"\b{re.escape(sym)}\b", desc.lower()):
                    out.append({"from": aid, "to": n["id"], "mechanism": "pricing", "status": "derived",
                                "derived": "observes_unknown", "exp": 1, "evidence": n.get("evidence", [])})
            continue
        for sym, exp in rel:
            aid = by_sym.get(sym.lower()) or by_sym.get(_ALIASES.get(sym, "").lower())
            if aid:
                out.append({"from": aid, "to": n["id"], "mechanism": "pricing", "status": "derived", "derived": "observes",
                            "exp": exp, "evidence": n.get("evidence", [])})
    return out


# ---------------------------------------------------------------------------
# Moteur
# ---------------------------------------------------------------------------

AFFECTED = ("origin", "changed", "unknown", "estimate")


def compatible_scenarios(graph, node_id):
    n = next((x for x in graph["nodes"] if x["id"] == node_id), None)
    if not n:
        return []
    return [k for k, v in SCENARIOS.items() if n["kind"] in v["kinds"]]


class Engine:
    def __init__(self, graph, ctx, scenario, node_id, severity, hours=DEFAULT_HOURS, secondary=True):
        if scenario not in SCENARIOS:
            raise ValueError(f"scénario inconnu : {scenario}")
        self.g, self.ctx = graph, ctx
        self.scenario, self.origin, self.hours, self.secondary = scenario, node_id, hours, secondary
        self.severity = min(max(float(severity), 0.0), SCENARIOS[scenario]["max"])
        self.nodes = {n["id"]: n for n in graph["nodes"]}
        if node_id not in self.nodes:
            raise ValueError(f"nœud inconnu : {node_id}")
        if self.nodes[node_id]["kind"] not in SCENARIOS[scenario]["kinds"]:
            raise ValueError(f"scénario {scenario} incompatible avec un nœud de type {self.nodes[node_id]['kind']}")
        self.edges = list(graph["edges"]) + derived_edges(graph)
        self.state = {nid: {"m": 1.0, "om": 1.0, "lm": 1.0, "status": "unchanged", "unknown": []} for nid in self.nodes}
        self.steps, self.secondary_effects, self.edge_reports = [], [], []

    # -- utilitaires -------------------------------------------------------
    def model_of(self, e):
        return classify(e, self.nodes, self.g)

    def affected(self, nid):
        return self.state[nid]["status"] in AFFECTED

    def name(self, nid):
        return self.nodes[nid]["name"]

    def step(self, nid, variable, before, after, status, model, mechanism, inputs=(), extra=None):
        info = MODELS[model]
        rec = {"node": nid, "name": self.name(nid), "kind": self.nodes[nid]["kind"], "variable": variable,
               "before": before, "after": after, "status": status, "model": model, "mechanism": mechanism,
               "dependency": info["dependency"], "formula": info["formula"], "params": info["params"],
               "source": info["source"], "assumptions": list(info["assumptions"]),
               "via": [e["from"] for e in inputs], "unknown": list(self.state[nid]["unknown"])}
        if isinstance(before, (int, float)) and isinstance(after, (int, float)) and before:
            rec["change_pct"] = after / before - 1
        if extra:
            rec.update(extra)
        self.steps.append(rec)
        return rec

    def inherit_unknown(self, nid, inputs):
        for e in inputs:
            for u in self.state[e["from"]]["unknown"]:
                if u not in self.state[nid]["unknown"]:
                    self.state[nid]["unknown"].append(u)

    def flag_unknown(self, nid, text):
        if text not in self.state[nid]["unknown"]:
            self.state[nid]["unknown"].append(text)

    # -- parcours ----------------------------------------------------------
    def downstream_order(self):
        out = {}
        for e in self.edges:
            out.setdefault(e["from"], []).append(e["to"])
        reach, stack = {self.origin}, [self.origin]
        while stack:
            u = stack.pop()
            for v in out.get(u, []):
                if v not in reach:
                    reach.add(v)
                    stack.append(v)
        # Les ventes forcées (marché de prêt → actif ou AMM) sont une RÉTROACTION : elles peuvent fermer
        # une boucle (prix → oracle → liquidations → ventes → prix). L'ordre de calcul les ignore ;
        # feedback_pass() les signale ensuite sur tout l'aval.
        fwd = {}
        for e in self.edges:
            if e["from"] in reach and e["to"] in reach and self.model_of(e) != "forced_sales":
                fwd.setdefault(e["from"], []).append(e["to"])
        indeg = {n: 0 for n in reach}
        for vs in fwd.values():
            for v in vs:
                indeg[v] += 1
        order, queue = [], [n for n in reach if indeg[n] == 0]
        while queue:
            u = queue.pop(0)
            order.append(u)
            for v in fwd.get(u, []):
                indeg[v] -= 1
                if indeg[v] == 0:
                    queue.append(v)
        cyclic = [n for n in reach if n not in order]
        return order, cyclic

    def run(self):
        o, st = self.origin, self.state[self.origin]
        s = self.severity
        var = SCENARIOS[self.scenario]["variable"]
        if self.scenario == "price_shock":
            st["m"] = 1 - s
        elif self.scenario == "oracle_failure":
            st["om"] = 1 - s
        else:
            st["lm"] = 1 - s
        st["status"] = "origin"
        self.steps.append({"node": o, "name": self.name(o), "kind": self.nodes[o]["kind"], "variable": var,
                           "before": 1.0, "after": 1 - s, "change_pct": -s, "status": "origin", "model": None,
                           "mechanism": f"Défaillance appliquée : {var} × (1 − {s:.0%})", "via": [], "unknown": [],
                           "assumptions": [], "dependency": "Point de départ", "formula": f"{var} × {1 - s:.4f}"})
        order, cyclic = self.downstream_order()
        for nid in cyclic:
            self.flag_unknown(nid, "boucle détectée dans les dépendances : non modélisée")
            self.state[nid]["status"] = "unknown"
        pos_id = self.g["position"]["id"]
        for nid in order:
            if nid in (o, pos_id):
                continue
            inputs = [e for e in self.edges if e["to"] == nid and self.affected(e["from"])]
            if not inputs:
                continue
            for e in inputs:
                self.report_edge(e)
            kind = self.nodes[nid]["kind"]
            {"asset": self.eval_asset, "protocol": self.eval_asset, "oracle": self.eval_oracle,
             "lending": self.eval_lending, "venue": self.eval_venue}.get(kind, self.eval_other)(nid, inputs)
        self.feedback_pass()
        for e in self.edges:
            if e["to"] == pos_id and self.affected(e["from"]):
                self.report_edge(e)
        return self.eval_position()

    def feedback_pass(self):
        """Ventes forcées par un marché devenu liquidable : effet non chiffré, signalé sur tout l'aval."""
        out = {}
        for e in self.edges:
            out.setdefault(e["from"], []).append(e["to"])
        for e in self.edges:
            if self.model_of(e) != "forced_sales":
                continue
            src = self.state[e["from"]]
            if src.get("agg_liquidatable"):
                text = f"ventes forcées par {self.name(e['from'])} sur {self.name(e['to'])} (rétroaction non chiffrée)"
            elif src["status"] == "unknown" and abs(src["om"] - 1) < 1e-12:
                text = (f"ventes forcées possibles par {self.name(e['from'])} : prix de son oracle au mécanisme inconnu, "
                        f"liquidations impossibles à exclure")
            else:
                continue
            seen, stack = {e["to"]}, [e["to"]]
            while stack:
                u = stack.pop()
                self.flag_unknown(u, text)
                if self.state[u]["status"] in ("unchanged", "no_effect"):
                    self.state[u]["status"] = "unknown"
                for v in out.get(u, []):
                    if v not in seen:
                        seen.add(v)
                        stack.append(v)

    def report_edge(self, e):
        m = self.model_of(e)
        info = MODELS[m]
        self.edge_reports.append({"from": e["from"], "to": e["to"], "mechanism": e["mechanism"], "model": m,
                                  "status": e.get("status"), "dependency": info["dependency"],
                                  "transmission": info["transmission"], "formula": info["formula"],
                                  "params": info["params"], "source": info["source"],
                                  "assumptions": info["assumptions"], "quantified": info["quantified"]})

    # -- évaluateurs de nœuds ----------------------------------------------
    def eval_asset(self, nid, inputs):
        value, blocked = [], []
        for e in inputs:
            m = self.model_of(e)
            if m in ("wrapper_rate", "sy_wrap", "pt_backing"):
                value.append(e)
            elif m == "amm_exit":
                if self.state[e["from"]]["unknown"]:
                    self.inherit_unknown(nid, [e])
                else:
                    blocked.append((e, "retrait proportionnel : prix spot (référence) inchangé, seule la profondeur baisse"))
            elif m == "forced_sales":
                if self.state[e["from"]].get("agg_liquidatable"):
                    self.flag_unknown(nid, f"ventes forcées possibles par {self.name(e['from'])} (non chiffrées)")
                else:
                    blocked.append((e, "marché toujours sain : pas de ventes forcées"))
            else:
                self.flag_unknown(nid, f"{MODELS[m]['dependency']} ({self.name(e['from'])}) : {MODELS[m]['formula']}")
        self.inherit_unknown(nid, value)
        st = self.state[nid]
        ms = {round(self.state[e["from"]]["m"], 12) for e in value}
        if len(ms) > 1:
            self.flag_unknown(nid, "plusieurs sources de valeur touchées : combinaison non modélisée")
        elif ms:
            st["m"] = ms.pop()
        price0 = self.ctx["prices_usd"].get(nid)
        changed = abs(st["m"] - 1) > 1e-12
        st["status"] = "unknown" if st["unknown"] and not changed else ("changed" if changed else
                                                                         ("unknown" if st["unknown"] else "no_effect"))
        if st["status"] == "no_effect" and not blocked:
            blocked = [(inputs[0], "aucune transmission")]
        model = self.model_of(value[0]) if value else self.model_of(inputs[0])
        mech = ("; ".join(why for _, why in blocked) if st["status"] == "no_effect" else
                f"prix de marché × {st['m']:.4f} via {', '.join(self.name(e['from']) for e in value)}" if value else
                "effet non chiffré")
        self.step(nid, "prix de marché (USD)", price0 if price0 else 1.0, (price0 or 1.0) * st["m"], st["status"], model, mech,
                  inputs, {"normalized": not price0})

    def eval_oracle(self, nid, inputs):
        n, st = self.nodes[nid], self.state[nid]
        typ = (n.get("details") or {}).get("type")
        self.inherit_unknown(nid, inputs)
        models = {self.model_of(e) for e in inputs}
        if "observes_unknown" in models:
            self.flag_unknown(nid, f"{n['name']} : mécanisme du flux non identifié")
        if typ == "meta_oracle":
            return self.eval_meta(nid, inputs)
        if models <= {"observes", "observes_unknown"}:
            om = 1.0
            for e in inputs:
                if e.get("derived") == "observes":
                    om *= self.state[e["from"]]["m"] ** e["exp"]
            st["om"] = om
            model, mech = "observes", f"le flux lit le prix de marché ({', '.join(self.name(e['from']) for e in inputs)})"
        elif typ == "morpho_chainlink_v2" and models <= {"feed_combination"}:
            om = 1.0
            parts = []
            for e in inputs:
                role = (e.get("details") or {}).get("role", "")
                exp = -1 if role.upper().startswith("QUOTE") else 1
                om *= self.state[e["from"]]["om"] ** exp
                parts.append(f"{role} × {self.state[e['from']]['om']:.4f}" + (" (au dénominateur)" if exp < 0 else ""))
            st["om"] = om
            model, mech = "feed_combination", "produit des flux : " + ", ".join(parts)
        else:
            self.flag_unknown(nid, f"{n['name']} : combinaison de prix non décodée")
            model, mech = "not_modeled", "INCONNU"
        changed = abs(st["om"] - 1) > 1e-12
        st["status"] = "changed" if changed else ("unknown" if st["unknown"] else "no_effect")
        self.step(nid, "prix rapporté (relatif)", 1.0, st["om"], st["status"], model,
                  mech if changed or st["unknown"] else mech + " → inchangé", inputs)

    def eval_meta(self, nid, inputs):
        n, st = self.nodes[nid], self.state[nid]
        mt, det = n.get("metrics") or {}, n.get("details") or {}
        src = {}
        for e in self.edges:
            if e["to"] == nid and self.model_of(e) == "meta_source":
                src[(e.get("details") or {}).get("role")] = e["from"]
        p_id, b_id = src.get("primaire"), src.get("secours")
        p0 = (self.ctx["oracles"].get(p_id) or {}).get("raw")
        b0 = (self.ctx["oracles"].get(b_id) or {}).get("raw")
        cur0 = (self.ctx["oracles"].get(nid) or {}).get("raw")
        thr, ch, he = mt.get("deviation_threshold"), mt.get("challenge_timelock_h"), mt.get("healing_timelock_h")
        if not (p_id and b_id and thr is not None and ch is not None):
            self.flag_unknown(nid, "méta-oracle : paramètres incomplets")
            st["status"] = "unknown"
            self.step(nid, "prix rapporté (relatif)", 1.0, 1.0, "unknown", "meta_source", "INCONNU", inputs)
            return
        assumed_equal = not (p0 and b0)
        p0 = p0 or 1.0
        b0 = b0 or p0
        p1, b1 = p0 * self.state[p_id]["om"], b0 * self.state[b_id]["om"]
        dev = abs(p1 - b1) / ((p1 + b1) / 2)
        using0 = det.get("using", "primaire")
        using = using0
        if using0 == "primaire" and dev > thr and self.hours >= ch:
            using = "secours"
        elif using0 == "secours" and dev <= thr and self.hours >= (he or 0):
            using = "primaire"
        base = cur0 or (p0 if using0 == "primaire" else b0)
        new = p1 if using == "primaire" else b1
        st["om"] = new / base
        st["meta"] = {"deviation": dev, "threshold": thr, "using_before": using0, "using_after": using,
                      "challenge_h": ch, "hours": self.hours, "primary_m": p1 / p0, "backup_m": b1 / b0}
        if dev > thr and self.hours < ch:
            mech = (f"écart primaire/secours {dev:.2%} > seuil {thr:.2%}, mais délai de contestation {ch:.0f} h non écoulé "
                    f"(incident de {self.hours:.0f} h) : reste sur le {using0}")
        elif using != using0:
            mech = f"écart {dev:.2%} > seuil {thr:.2%} pendant ≥ {ch:.0f} h : bascule {using0} → {using}"
        else:
            mech = f"écart {dev:.2%} ≤ seuil {thr:.2%} : reste sur le {using0}"
        self.inherit_unknown(nid, [e for e in inputs if self.nodes[e["from"]]["id"] == (p_id if using == "primaire" else b_id)])
        changed = abs(st["om"] - 1) > 1e-12
        st["status"] = "changed" if changed else ("unknown" if st["unknown"] else "no_effect")
        extra = {"meta": st["meta"]}
        if assumed_equal:
            extra["assumptions_extra"] = ["price() des sources indisponible : primaire = secours supposés au départ"]
        self.step(nid, "prix rapporté (relatif)", 1.0, st["om"], st["status"], "meta_source", mech, inputs, extra)

    def eval_lending(self, nid, inputs):
        n, st = self.nodes[nid], self.state[nid]
        lc = self.ctx["lending"].get(nid)
        self.inherit_unknown(nid, [e for e in inputs if self.model_of(e) in ("oracle_to_lending", "collateral_value")])
        for e in inputs:
            m = self.model_of(e)
            if m == "oracle_to_lending":
                st["om"] = self.state[e["from"]]["om"] if lc else st["om"]
                if not lc:
                    self.flag_unknown(nid, f"prix {(e.get('details') or {}).get('asset', '')} vu par {n['name']} : "
                                           f"× {self.state[e['from']]['om']:.4f} (créances douteuses d'un pool multi-actifs non modélisées)")
            elif m == "collateral_value":
                st["m"] = self.state[e["from"]]["m"]
            elif m == "pool_bad_debt":
                self.flag_unknown(nid, f"{self.name(e['from'])} touché : créance douteuse d'Aave non modélisée")
            else:
                self.flag_unknown(nid, f"{MODELS[m]['dependency']} : non modélisé")
        extra = {}
        if lc and lc.get("borrow_usd") and lc.get("collateral_usd") and lc.get("lltv"):
            ltv0 = lc["borrow_usd"] / lc["collateral_usd"]
            hf0 = lc["lltv"] / ltv0
            hf1 = hf0 * st["om"]
            st["agg_liquidatable"] = hf1 < 1 <= hf0
            extra["aggregate"] = {"ltv0": ltv0, "hf0": hf0, "hf1": hf1, "liquidatable": st["agg_liquidatable"],
                                  "note": "emprunteur représentatif (agrégat du marché) : ESTIMATION"}
            if st["agg_liquidatable"] and self.secondary:
                self.forced_sale_estimate(nid, lc, st)
        changed = abs(st["om"] - 1) > 1e-12 or abs(st["m"] - 1) > 1e-12
        st["status"] = "changed" if changed else ("unknown" if st["unknown"] else "no_effect")
        mech = (f"collatéral vu par l'oracle × {st['om']:.4f}, valeur de marché × {st['m']:.4f}" if lc else
                "pool multi-actifs : effet non chiffré")
        agg = extra.get("aggregate")
        if agg:
            mech += f" ; santé agrégée du marché {agg['hf0']:.3f} → {agg['hf1']:.3f}" + (" (liquidable)" if agg["liquidatable"] else "")
        self.step(nid, "collatéral vu par le protocole (relatif)", 1.0, st["om"], st["status"],
                  "oracle_to_lending" if lc else "pool_bad_debt", mech, inputs, extra)

    def forced_sale_estimate(self, nid, lc, st):
        """Effet secondaire : si tout le marché était liquidé, combien de collatéral serait revendu ?"""
        seized_frac = min(1.0, lc["borrow_usd"] * lc["lif"] / (lc["collateral_usd"] * st["om"]))
        for e in self.edges:
            if e["from"] != nid or self.model_of(e) != "forced_sales":
                continue
            eff = {"from": nid, "to": e["to"], "included_in_total": False, "seized_fraction": seized_frac,
                   "seized_usd": seized_frac * lc["collateral_usd"],
                   "why": "Liquidations du marché (emprunteur représentatif, liquidation totale) : collatéral revendu par les liquidateurs"}
            pm = self.ctx["pendle"].get(e["to"])
            if pm:
                mk = amm.PendleMarket.from_state(pm).scaled(self.state[e["to"]]["lm"])
                price = self.ctx["prices_usd"].get(pm["pt_node"])
                units = seized_frac * lc["collateral_usd"] / price if price else None
                if units:
                    r = mk.sell_pt(units)
                    if r["ok"]:
                        after = mk.after_sale(units, r["asset_out"])
                        eff["spot_change"] = after.spot_pt_to_asset() / mk.spot_pt_to_asset() - 1
                        eff["text"] = (f"vente de {units:,.0f} PT sur l'AMM : prix spot du PT {eff['spot_change']:+.2%}")
                    else:
                        eff["sellable_share"] = mk.max_pt_sellable() / units
                        eff["text"] = (f"{units:,.0f} PT à revendre, l'AMM n'en absorbe que {mk.max_pt_sellable():,.0f} "
                                       f"({eff['sellable_share']:.0%}) : liquidations difficiles, créance douteuse possible")
            else:
                eff["text"] = "lieu de revente non modélisé : effet sur le prix INCONNU"
            self.secondary_effects.append(eff)

    def eval_venue(self, nid, inputs):
        st = self.state[nid]
        self.inherit_unknown(nid, inputs)
        for e in inputs:
            if self.model_of(e) == "forced_sales" and self.state[e["from"]].get("agg_liquidatable"):
                self.flag_unknown(nid, f"ventes forcées par {self.name(e['from'])} (voir effets secondaires, non incluses)")
        st["status"] = "unknown" if st["unknown"] else "no_effect"
        self.step(nid, "liquidité du pool (relative)", 1.0, st["lm"], st["status"], "forced_sales",
                  "ventes forcées possibles : estimation séparée" if st["unknown"] else "marchés de prêt sains : pas de ventes forcées",
                  inputs)

    def eval_other(self, nid, inputs):
        self.inherit_unknown(nid, inputs)
        for e in inputs:
            m = self.model_of(e)
            self.flag_unknown(nid, f"{self.name(e['from'])} → {self.name(nid)} : {MODELS[m]['formula']}")
        self.state[nid]["status"] = "unknown"
        self.step(nid, "état", None, None, "unknown", self.model_of(inputs[0]), "INCONNU : non modélisé", inputs)

    # -- position ----------------------------------------------------------
    def m_of(self, nid):
        return self.state[nid]["m"] if nid else 1.0

    def market_for(self, venue, baseline=False):
        pm = self.ctx["pendle"].get(venue) if venue else None
        if not pm:
            return None
        return amm.PendleMarket.from_state(pm).scaled(1.0 if baseline else self.state[venue]["lm"])

    def exit_value(self, venue, units, sy_price_usd, baseline=False):
        """Vente de `units` PT sur l'AMM : produit en USD, part vendable, coût d'exécution."""
        mk = self.market_for(venue, baseline)
        if mk is None or not units or not sy_price_usd:
            return None
        r = mk.sell_pt(units)
        full = r["ok"]
        sold = units if full else min(units, mk.max_pt_sellable() * 0.999)
        r = r if full else mk.sell_pt(sold)
        return {"full_exit": full, "units": units, "sold_units": sold, "locked_units": units - sold,
                "proceeds_usd": r["sy_out"] * sy_price_usd if r.get("ok") else 0.0,
                "price_impact": r.get("price_impact"), "fee_usd": (r.get("fee_asset") or 0) / mk.sy_rate * sy_price_usd,
                "max_sellable_units": mk.max_pt_sellable(), "pool_liquidity_usd": mk.liquidity_asset() / mk.sy_rate * sy_price_usd}

    def eval_position(self):
        pos = self.g["position"]
        pid = pos["id"]
        pc = self.ctx["position"]
        typ = pc["type"]
        incoming = [e for e in self.edges if e["to"] == pid]
        affected_in = [e for e in incoming if self.affected(e["from"])]
        self.inherit_unknown(pid, affected_in)
        for e in affected_in:
            m = self.model_of(e)
            if not MODELS[m]["quantified"] and m != "forced_sales":
                self.flag_unknown(pid, f"{MODELS[m]['dependency']} ({self.name(e['from'])}) : non modélisé")
            if m == "forced_sales" and self.state[e["from"]].get("agg_liquidatable"):
                self.flag_unknown(pid, f"ventes forcées par {self.name(e['from'])} (non chiffrées)")
        fn = {"pendle_pt": self.pos_pendle, "morpho_borrow": self.pos_borrow, "hold": self.pos_hold,
              "aave_supply": self.pos_supply, "morpho_supply": self.pos_supply}.get(typ)
        res = fn(incoming) if fn else {"error": f"type de position non pris en charge : {typ}"}
        res["unknown"] = list(self.state[pid]["unknown"])
        return res

    def _sy_price(self, venue):
        pm = self.ctx["pendle"].get(venue) or {}
        return self.ctx["prices_usd"].get(pm.get("sy_node")), pm.get("sy_node")

    def pos_pendle(self, incoming):
        pc, pid = self.ctx["position"], self.g["position"]["id"]
        sy_edge = next((e for e in incoming if self.model_of(e) == "pt_backing"), None)
        m = self.m_of(sy_edge["from"]) if sy_edge else 1.0
        if sy_edge and self.affected(sy_edge["from"]):
            self.step(pid, "prix de marché du PT (USD)", self.ctx["prices_usd"].get(pid), (self.ctx["prices_usd"].get(pid) or 0) * m,
                      "changed" if abs(m - 1) > 1e-12 else "no_effect", "pt_backing", f"P_PT × {m:.4f} (taux implicite constant)", [sy_edge])
        size = pc["size_usd"]
        sy_p, sy_node = self._sy_price(pc.get("venue"))
        ex0 = self.exit_value(pc.get("venue"), pc.get("units"), sy_p, baseline=True)
        ex1 = self.exit_value(pc.get("venue"), pc.get("units"), sy_p * self.m_of(sy_node) if sy_p else None)
        if ex1 and not ex1["full_exit"]:
            ex1["compare"] = self.exit_value(pc.get("venue"), ex1["sold_units"], sy_p, baseline=True)
        if ex1 and self.affected(pc.get("venue") or ""):
            self.step(pid, "produit de sortie exécutable (USD)", ex0["proceeds_usd"], ex1["proceeds_usd"],
                      "changed", "amm_exit", self._exit_text(ex1), [e for e in incoming if self.model_of(e) == "amm_exit"])
        return self.finish(ref0=size, ref1=size * m, liq1=size * m, ex0=ex0, ex1=ex1, lending=None,
                           exposure={"Valeur de marché du PT": (size, size * m)})

    def pos_hold(self, incoming):
        e = next((e for e in incoming if self.model_of(e) == "position_holds"), None)
        m = self.m_of(e["from"]) if e else 1.0
        size = self.ctx["position"]["size_usd"]
        return self.finish(ref0=size, ref1=size * m, liq1=size * m, ex0=None, ex1=None, lending=None,
                           exposure={"Valeur de marché": (size, size * m)},
                           exec_note="Aucun pool de sortie dans le graphe pour cet actif : coût d'exécution non calculable")

    def pos_supply(self, incoming):
        pc = self.ctx["position"]
        e = next((e for e in incoming if self.model_of(e) == "position_loan_asset"), None)
        m_loan = self.m_of(e["from"]) if e else 1.0
        size = pc["size_usd"]
        lid = pc.get("lending")
        lc = self.ctx["lending"].get(lid)
        bad0 = bad1 = 0.0
        note = None
        if lc and lc.get("supply_usd"):
            share = size / lc["supply_usd"]
            m_coll = self.state[lid]["m"]
            bad0 = share * max(0.0, lc["borrow_usd"] - lc["collateral_usd"])
            bad1 = share * max(0.0, lc["borrow_usd"] * m_loan - lc["collateral_usd"] * m_coll)
            note = (f"ESTIMATION : part du dépôt {share:.1%} × max(0, dette {lc['borrow_usd'] * m_loan:,.0f} $ − collatéral "
                    f"{lc['collateral_usd'] * m_coll:,.0f} $), emprunteurs homogènes")
        elif lid and self.affected(lid):
            self.flag_unknown(self.g["position"]["id"], "créance douteuse du pool non modélisée")
        ref0, ref1 = size - bad0, size * m_loan
        liq1 = ref1 - bad1
        avail = pc.get("available_liquidity_usd")
        exec_note = (f"Retrait immédiat limité par la liquidité disponible du marché : {avail:,.0f} $ "
                     f"({min(1, avail / size):.0%} de la position) ; le reste est différé, pas perdu" if avail is not None else None)
        return self.finish(ref0=ref0, ref1=ref1, liq1=liq1, ex0=None, ex1=None, lending=None,
                           exposure={"Valeur du dépôt": (size, ref1)}, exec_note=exec_note, liq_note=note,
                           liq_label="Créance douteuse (estimation)")

    def pos_borrow(self, incoming):
        pc, pid = self.ctx["position"], self.g["position"]["id"]
        lid = pc["lending"]
        lc = self.ctx["lending"].get(lid) or {}
        q, d = pc.get("collateral_units"), pc["debt_units"]
        p_coll0 = self.ctx["prices_usd"].get(pc["collateral_node"])
        p_debt0 = self.ctx["prices_usd"].get(pc["debt_node"], 1.0)
        po0 = oracle_price_loan_units(self.ctx, lc.get("oracle"), lid)
        if not (q and p_coll0 and po0 and lc.get("lltv")):
            self.flag_unknown(pid, "données de prêt incomplètes (prix, oracle ou LLTV) : santé non calculable")
            return {"error": "données insuffisantes", "unknown": self.state[pid]["unknown"]}
        m_c, m_d = self.m_of(pc["collateral_node"]), self.m_of(pc["debt_node"])
        om = self.state[lid]["om"]
        po1 = po0 * om
        lltv, lif = lc["lltv"], lc["lif"]
        coll_o0, coll_o1 = q * po0, q * po1            # en unités de dette (USDC), vu par Morpho
        ltv0, ltv1 = d / coll_o0, d / coll_o1
        hf0, hf1 = coll_o0 * lltv / d, coll_o1 * lltv / d
        eligible = hf1 < 1
        p_c1, p_d1 = p_coll0 * m_c, p_debt0 * m_d
        ref0 = q * p_coll0 - d * p_debt0
        ref1 = q * p_c1 - d * p_d1
        liq = None
        q_left, d_left = q, d
        if eligible:
            seized = min(q, d * lif / po1)
            repaid = seized * po1 / lif
            bad_debt = d - repaid if seized >= q - 1e-9 else 0.0
            q_left, d_left = q - seized, (0.0 if bad_debt else d - repaid)
            liq = {"seized_units": seized, "seized_usd_market": seized * p_c1, "repaid": repaid,
                   "penalty_usd": seized * p_c1 - repaid * p_d1, "bad_debt_units": bad_debt,
                   "collateral_left_units": q_left, "debt_left": d_left}
        liq1 = q_left * p_c1 - d_left * p_d1
        venue = pc.get("venue")
        sy_p, sy_node = self._sy_price(venue)

        def unwind(units, debt, price_mult, p_d, baseline=False):
            ex = self.exit_value(venue, units, sy_p * price_mult if sy_p else None, baseline)
            if ex is None:
                return None
            ex = dict(ex)
            ex["equity_usd"] = ex["proceeds_usd"] - debt * p_d
            ex["ref_sold_usd"] = ex["sold_units"] * (p_coll0 * price_mult)
            return ex

        ex0 = unwind(q, d, 1.0, p_debt0, baseline=True)
        ex1 = unwind(q_left, d_left, self.m_of(sy_node), p_d1)
        if ex1 and not ex1["full_exit"]:
            ex1["compare"] = unwind(ex1["sold_units"], 0.0, 1.0, p_debt0, baseline=True)
        lend = {"lltv": lltv, "lif": lif, "oracle_price0": po0, "oracle_price1": po1,
                "market_price0": p_coll0, "market_price1": p_c1,
                "collateral_oracle0": coll_o0, "collateral_oracle1": coll_o1,
                "collateral_market0": q * p_coll0, "collateral_market1": q * p_c1,
                "debt_usd0": d * p_debt0, "debt_usd1": d * p_d1, "debt_units": d, "collateral_units": q,
                "ltv0": ltv0, "ltv1": ltv1, "hf0": hf0, "hf1": hf1, "eligible": eligible, "liquidation": liq,
                "oracle_vs_market0": po0 / (p_coll0 / p_debt0) - 1, "oracle_vs_market1": po1 / (p_c1 / p_d1) - 1}
        self.step(pid, "facteur de santé", hf0, hf1, "changed" if abs(hf1 - hf0) > 1e-12 else "no_effect",
                  "liquidation_rule",
                  f"HF = Q × P_oracle × LLTV / dette = {q:,.0f} × {po1:.4f} × {lltv} / {d:,.0f} = {hf1:.4f}"
                  + (" → LIQUIDABLE" if eligible else " → sain"),
                  [e for e in incoming if self.affected(e["from"])])
        return self.finish(ref0=ref0, ref1=ref1, liq1=liq1, ex0=ex0, ex1=ex1, lending=lend,
                           exposure={"Collatéral (marché)": (q * p_coll0, q * p_c1), "Dette": (d * p_debt0, d * p_d1),
                                     "Fonds propres": (ref0, ref1)})

    def _exit_text(self, ex):
        if ex["full_exit"]:
            return f"vente de {ex['units']:,.0f} PT : impact {ex['price_impact']:.2%}, produit {ex['proceeds_usd']:,.0f} $"
        return (f"sortie complète IMPOSSIBLE : l'AMM n'absorbe que {ex['max_sellable_units']:,.0f} PT sur {ex['units']:,.0f} "
                f"(proportion max 96 %)")

    # -- métriques ---------------------------------------------------------
    def finish(self, ref0, ref1, liq1, ex0, ex1, lending, exposure, exec_note=None, liq_note=None,
               liq_label="Perte de liquidation"):
        def metric(key, label, initial, after, method, status="calculé", loss=None, note=None):
            out = {"key": key, "label": label, "initial": initial, "after": after, "method": method, "status": status}
            if initial is not None and after is not None:
                out["change_usd"] = after - initial
                out["change_pct"] = (after - initial) / ref0 if ref0 else None
                out["loss_usd"] = loss if loss is not None else initial - after
            if note:
                out["note"] = note
            return out

        metrics = [metric("market", "Perte de marché (valeur de marché de la position)", ref0, ref1,
                          "Valeur de marché recalculée par le graphe (prix de marché, pas l'oracle)")]
        if lending is not None or liq_note:
            eligible = lending["eligible"] if lending else liq1 < ref1 - 1e-6
            metrics.append(metric("liquidation", liq_label, ref1, liq1,
                                  ("Liquidation Morpho simulée : saisie = dette × LIF / P_oracle, valorisée au prix de marché"
                                   if lending else "Créance douteuse estimée (emprunteur représentatif)"),
                                  status=("calculé — SI la liquidation est exécutée" if lending and eligible else
                                          "aucune liquidation (position saine)" if lending else "estimation"),
                                  note=liq_note))
        if ex0 and ex1:
            # Sortie complète possible : coût avant (position entière) vs après (ce qui reste) → somme télescopique.
            # Sortie impossible : on compare le coût de vente de la MÊME quantité (la part encore vendable).
            unit0 = ref0 / ex0["units"] if ex0["units"] and not lending else None
            unit1 = ref1 / ex1["units"] if ex1["units"] and not lending else None
            base = ex0 if ex1["full_exit"] else ex1["compare"]
            ref_sold0 = base.get("ref_sold_usd", base["sold_units"] * (unit0 or 0))
            ref_sold1 = ex1.get("ref_sold_usd", ex1["sold_units"] * (unit1 or 0))
            c0, c1 = ref_sold0 - base["proceeds_usd"], ref_sold1 - ex1["proceeds_usd"]
            metrics.append(metric("execution", "Perte d'exécution (valeur de référence − produit exécutable)", c0, c1,
                                  "Courbe Pendle V2 sur l'AMM (readState on-chain), AMM seul ; après l'incident, "
                                  "sur ce qui reste de la position (après liquidation éventuelle)", loss=c1 - c0,
                                  status="calculé" if ex1["full_exit"] else "calculé sur la partie vendable — sortie complète impossible",
                                  note=None if ex1["full_exit"] else
                                  f"comparaison sur la même quantité : {ex1['sold_units']:,.0f} PT vendables sur {ex1['units']:,.0f}"))
            if not ex1["full_exit"]:
                unit_ref = (ex1.get("ref_sold_usd", 0) / ex1["sold_units"]) if lending and ex1["sold_units"] else (unit1 or 0)
                metrics.append({"key": "blocked", "label": "Valeur non exécutable maintenant (sortie complète impossible)",
                                "initial": 0.0, "after": ex1["locked_units"] * unit_ref, "excluded_from_total": True,
                                "status": "non incluse dans le total : invendable sur l'AMM, remboursable à l'échéance du PT",
                                "method": "PT au-delà de la proportion maximale de 96 % du pool, valorisés au prix de référence",
                                "note": f"{ex1['locked_units']:,.0f} PT bloqués jusqu'à l'échéance ou au retour de la liquidité"})
        else:
            metrics.append({"key": "execution", "label": "Perte d'exécution", "initial": None, "after": None,
                            "status": "information insuffisante", "method": exec_note or "Aucun modèle de sortie", "note": exec_note})
        known = [m for m in metrics if m.get("loss_usd") is not None]
        total_loss = sum(m["loss_usd"] for m in known)
        total = {"label": "Impact financier total", "initial": ref0, "after": ref0 - total_loss, "change_usd": -total_loss,
                 "change_pct": -total_loss / ref0 if ref0 else None, "loss_usd": total_loss,
                 "components": [m["key"] for m in known],
                 "method": "Somme télescopique sans double compte : (référence avant − référence après) + "
                           "(sans liquidation − avec liquidation) + (coût de sortie après − coût de sortie avant)"}
        if lending and not lending["eligible"]:
            total["note"] = "Position non liquidable dans ce scénario : aucune perte de liquidation"
        elif lending:
            total["note"] = "Inclut la liquidation simulée ; éligible ne veut pas dire liquidée"
        total["loss_without_liquidation"] = sum(m["loss_usd"] for m in known if m["key"] != "liquidation")
        # Part invendable maintenant : hors du total (remboursable à l'échéance), mais perdue si l'on DOIT sortir tout de suite.
        blocked = next((m["after"] for m in metrics if m["key"] == "blocked"), 0.0)
        total["full_exit_possible"] = not blocked
        total["blocked_usd"] = blocked
        total["loss_if_forced_exit_usd"] = total_loss + blocked
        total["after_if_forced_exit"] = ref0 - total_loss - blocked
        return {"metrics": metrics, "total": total, "lending": lending, "exit_before": ex0, "exit_after": ex1,
                "exposure": {k: {"initial": a, "after": b} for k, (a, b) in exposure.items()},
                "equity_before": ref0, "equity_after_market": ref1}


def simulate(graph, ctx, scenario, node_id, severity, hours=DEFAULT_HOURS, with_threshold=True):
    """Point d'entrée : renvoie le résultat complet (état des nœuds, étapes, métriques)."""
    eng = Engine(graph, ctx, scenario, node_id, severity, hours)
    res = eng.run()
    affected = {nid: {"status": st["status"], "m": st["m"], "om": st["om"], "lm": st["lm"], "unknown": st["unknown"]}
                for nid, st in eng.state.items() if st["status"] != "unchanged"}
    out = {"scenario": scenario, "scenario_label": SCENARIOS[scenario]["label"], "node": node_id,
           "node_name": eng.name(node_id), "severity": eng.severity, "hours": hours,
           "position": {"id": graph["position"]["id"], "label": graph["position"]["label"],
                        "type": graph["position"].get("type")},
           "result": res, "steps": eng.steps, "affected": affected, "edges": eng.edge_reports,
           "secondary_effects": eng.secondary_effects, "path": main_path(graph, eng),
           "data": {"evidence": ctx.get("evidence", []), "missing": ctx.get("missing", []),
                    "assumed_prices": ctx.get("assumed_prices", [])}}
    if with_threshold and res.get("lending"):
        out["liquidation_threshold"] = min_shock_to_liquidation(graph, ctx, scenario, node_id, hours)
    return out


def main_path(graph, eng):
    """Chemin de propagation (nœuds réellement modifiés) de l'origine à la position."""
    pos = graph["position"]["id"]
    parents, queue, seen = {}, [eng.origin], {eng.origin}
    while queue:
        u = queue.pop(0)
        for e in eng.edges:
            if e["from"] == u and e["to"] not in seen and (eng.state[e["to"]]["status"] in ("changed", "unknown") or e["to"] == pos):
                seen.add(e["to"])
                parents[e["to"]] = (u, e)
                queue.append(e["to"])
    if pos not in parents:
        return []
    path, cur = [], pos
    while cur != eng.origin:
        u, e = parents[cur]
        path.append({"from": u, "to": cur, "mechanism": e["mechanism"], "model": eng.model_of(e)})
        cur = u
    return list(reversed(path))


def min_shock_to_liquidation(graph, ctx, scenario, node_id, hours=DEFAULT_HOURS):
    """Plus petite sévérité de CE scénario qui rend la position liquidable (dichotomie)."""
    cap = SCENARIOS[scenario]["max"]

    def eligible(s):
        res = Engine(graph, ctx, scenario, node_id, s, hours, secondary=False).run()
        return bool((res.get("lending") or {}).get("eligible"))

    if eligible(0.0):
        return {"severity": 0.0, "text": "déjà liquidable avant l'incident"}
    if not eligible(cap):
        return {"severity": None, "text": f"aucune sévérité jusqu'à {cap:.0%} ne rend la position liquidable par ce chemin"}
    lo, hi = 0.0, cap
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if eligible(mid) else (mid, hi)
    return {"severity": hi, "text": f"liquidable à partir d'une sévérité de {hi:.2%}"}
