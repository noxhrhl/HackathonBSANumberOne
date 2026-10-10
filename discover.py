"""Découverte automatique des dépendances d'une position (bibliothèque standard uniquement).

Principe : parcours en largeur depuis la position. Chaque nœud découvert est confié à un
« résolveur » selon son type, qui cherche ce dont il dépend dans les données réelles :

  PendlePT   : PT → contrat SY (on-chain) → actif sous-jacent (on-chain), AMM Pendle, chaîne
  Collatéral : qui accepte cet actif en garantie (API Morpho, API Aave) — seulement si un
               marché réel existe (identifiant de marché enregistré)
  Oracle     : quel oracle prix ce marché ; décodé on-chain (primaire/secours, flux, type)
  Actif      : enveloppe ERC-4626 → actif de base (on-chain), actif → émetteur (table documentée)
  Émetteur   : dépendances critiques de l'émetteur (bourses de couverture via l'API Ethena,
               dépositaires et gouvernance via la documentation officielle)

Direction : « A → B » = si A défaille, B est touché. La position est le seul puits.
Une arête qui fermerait une boucle n'est pas ajoutée : la boucle est signalée à part.

Usage : python discover.py [position_id] [--offline]
"""

import json
import sys
from collections import deque
from pathlib import Path

import sources as S
from keccak import selector
from schema import coverage, edge, node, validate

ROOT = Path(__file__).resolve().parent
DOCUMENTED = json.loads((ROOT / "data" / "documented.json").read_text(encoding="utf-8"))
POSITIONS = json.loads((ROOT / "data" / "positions.json").read_text(encoding="utf-8"))
MAX_DEPTH = 5             # 5 étages, comme l'exemple de Belem
MIN_COLLATERAL_USD = 1e6  # on ignore les marchés de prêt quasi vides
TOP_COLLATERAL_MARKETS = 2
TOP_POOL_COLLATERALS = 8
DOC = "documentation"


def doc_ev(url):
    return [{"source": DOC, "url": url, "fetched_at": DOCUMENTED["checked_at"]}]


def api_ev(source, key):
    return [S.meta(source, key)]


def chain_ev(address, fn):
    """Preuve on-chain : lien Etherscan « Read Contract » + heure du relevé RPC."""
    m = S.meta("rpc", f"call_{address.lower()}_{selector(fn)}")
    return [{"source": f"eth_call {fn}", "url": f"https://etherscan.io/address/{address.lower()}#readContract",
             "fetched_at": m["fetched_at"]}]


def call(address, fn):
    return S.eth_call(address, selector(fn))


def short(addr):
    return addr[:6] + "…" + addr[-4:]


def token_symbol(address):
    info = DOCUMENTED["tokens"].get(address.lower())
    if info:
        return info["symbol"]
    sym = S.dec_string(call(address, "symbol()"))
    return sym or short(address)


class Builder:
    """Accumule nœuds et arêtes ; refuse les arêtes qui créeraient un cycle."""

    def __init__(self, position_id):
        self.position_id = position_id
        self.nodes = {}
        self.edges = []
        self.loops = []
        self.queue = deque()
        self.expanded = set()

    def add_node(self, n):
        if n["id"] in self.nodes:
            old = self.nodes[n["id"]]
            old["metrics"].update({k: v for k, v in n["metrics"].items() if v is not None})
            for ev in n["evidence"]:
                if ev not in old["evidence"]:
                    old["evidence"].append(ev)
            for f in n.get("flags", []):
                old.setdefault("flags", [])
                if f not in old["flags"]:
                    old["flags"].append(f)
            old.setdefault("details", {}).update(n.get("details", {}))
        else:
            self.nodes[n["id"]] = n
        return n["id"]

    def _reaches(self, start, goal):
        adj = {}
        for e in self.edges:
            adj.setdefault(e["from"], []).append(e["to"])
        seen, stack = {start}, [start]
        while stack:
            u = stack.pop()
            if u == goal:
                return True
            for v in adj.get(u, []):
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        return False

    def add_edge(self, e):
        if any(x["from"] == e["from"] and x["to"] == e["to"] for x in self.edges):
            return False
        if e["from"] == e["to"] or self._reaches(e["to"], e["from"]):
            note = {"collateral_liquidation": "spirale de liquidation possible : les ventes forcées font baisser le prix "
                                              "du collatéral, ce qui déclenche d'autres liquidations",
                    "backing": "dépendance circulaire : chacun détient une partie de l'autre (risque réflexif)"}.get(
                e["mechanism"], "boucle réflexive")
            self.loops.append({"from": e["from"], "to": e["to"], "mechanism": e["mechanism"],
                               "note": note + " — arête signalée ici, non dessinée (le graphe reste acyclique)",
                               "evidence": e["evidence"]})
            return False
        self.edges.append(e)
        return True

    def enqueue(self, kind, node_id, depth, **ctx):
        if depth < MAX_DEPTH and (kind, node_id) not in self.expanded:
            self.expanded.add((kind, node_id))
            self.queue.append((kind, node_id, depth, ctx))


# ---------------------------------------------------------------------------
# Nœuds partagés (identifiants canoniques, identiques d'une position à l'autre)
# ---------------------------------------------------------------------------

def chain_node(b, chain_id):
    info = DOCUMENTED["chains"]["ethereum"]
    tvl = S.llama_chain_tvl("Ethereum")
    return b.add_node(node("chain:ethereum", "Ethereum (chaîne de règlement)", "chain",
                           metrics={"tvl_usd": tvl}, evidence_list=api_ev("defillama", "chains_Ethereum")
                           + doc_ev(info["url"])))


def asset_node(b, address, chain_id=1):
    a = address.lower()
    return b.add_node(node(f"asset:{chain_id}:{a}", token_symbol(a), "asset", address=a,
                           evidence_list=[{"source": "etherscan", "url": f"https://etherscan.io/token/{a}",
                                           "fetched_at": DOCUMENTED["checked_at"]}]))


# ---------------------------------------------------------------------------
# Résolveurs de positions
# ---------------------------------------------------------------------------

def all_pts():
    return [p["pt"] for p in POSITIONS["positions"] if p["type"] == "pendle_pt"]


def pendle_market_for_pt(chain_id, pt):
    for m in S.pendle_markets(chain_id, all_pts()):
        if S.strip_chain(m["pt"]) == pt.lower():
            return m
    return None


def expand_pendle_pt(b, pt_node_id, pt, chain_id, depth, as_position=None):
    """PT → SY (on-chain) → sous-jacent (on-chain) ; AMM Pendle ; marchés de prêt sur ce PT."""
    m = pendle_market_for_pt(chain_id, pt)
    if m is None:
        return
    market = m["address"].lower()
    key_markets = f"markets_active_{chain_id}_" + "_".join(sorted(a.lower()[:10] for a in all_pts()))
    data = S.pendle_market_data(chain_id, market)
    tokens = S.dec_addresses(call(market, "readTokens()"), 3)  # (SY, PT, YT)
    sy = tokens[0] if tokens else S.strip_chain(m["sy"])
    status_sy = "verified_onchain" if tokens and tokens[1] == pt.lower() else "api"
    ev_sy = chain_ev(market, "readTokens()") if status_sy == "verified_onchain" else api_ev("pendle", key_markets)

    sy_id = b.add_node(node(f"protocol:pendle-sy:{chain_id}:{sy}", f"Contrat SY Pendle ({m['name']})", "protocol",
                            address=sy, evidence_list=ev_sy,
                            details={"role": "Le PT se rachète contre le SY à l'échéance"}))
    b.add_edge(edge(sy_id, pt_node_id, "backing", status_sy, ev_sy,
                    {"why": "À l'échéance, 1 PT = 1 unité du sous-jacent via le contrat SY"}))

    under = S.dec_address(call(sy, "yieldToken()")) or S.strip_chain(m["underlyingAsset"])
    status_u = "verified_onchain" if under == S.strip_chain(m["underlyingAsset"]) else "api"
    u_id = asset_node(b, under, chain_id)
    b.add_edge(edge(u_id, sy_id, "backing", status_u, chain_ev(sy, "yieldToken()"),
                    {"why": "Le SY détient le sous-jacent ; sa valeur fait celle du PT"}))
    b.enqueue("asset", u_id, depth + 1, address=under, chain_id=chain_id, collateral=as_position is not None)  # PT→SY→actif = un étage

    venue_id = b.add_node(node(f"venue:pendle:{chain_id}:{market}", f"AMM Pendle {m['name']} ({m['expiry'][:10]})",
                               "venue", address=market,
                               metrics={"liquidity_usd": data["liquidity"]["usd"],
                                        "volume_24h_usd": data["tradingVolume"]["usd"],
                                        "implied_apy": data["impliedApy"], "pt_discount": data["ptDiscount"],
                                        "total_pt": data["totalPt"], "total_sy": data["totalSy"]},
                               evidence_list=api_ev("pendle", f"market_data_{chain_id}_{market}")))
    b.add_edge(edge(venue_id, pt_node_id, "exit_liquidity", "api",
                    api_ev("pendle", f"market_data_{chain_id}_{market}"),
                    {"why": "Seul lieu pour vendre le PT avant l'échéance", "liquidity_usd": data["liquidity"]["usd"]}))
    b.enqueue("collateral", pt_node_id, depth, address=pt, chain_id=chain_id, venue=venue_id,
              venue_liquidity=data["liquidity"]["usd"])
    return {"market": m, "data": data, "sy": sy, "underlying": under, "venue": venue_id}


def resolve_position(b, pos):
    chain_id = pos["chain_id"]
    pid = f"position:{pos['id']}"
    b.add_node(node(pid, pos["label"], "position", metrics={"size_usd": pos["size_usd"]},
                    evidence_list=doc_ev("data/positions.json"), details={"type": pos["type"]}))
    ch = chain_node(b, chain_id)
    b.add_edge(edge(ch, pid, "settlement", "documented", doc_ev(DOCUMENTED["chains"]["ethereum"]["url"]),
                    {"why": "Toute sortie ou remboursement passe par une transaction Ethereum"}))
    info = {}
    if pos["type"] == "pendle_pt":
        res = expand_pendle_pt(b, pid, pos["pt"], chain_id, 0, as_position=pos)
        m, data = res["market"], res["data"]
        prices = S.pendle_prices(chain_id, [pos["pt"], res["underlying"]])
        pt_price = prices.get(pos["pt"].lower())
        b.nodes[pid]["address"] = pos["pt"].lower()
        b.nodes[pid]["metrics"].update({"pt_price_usd": pt_price, "expiry": m["expiry"],
                                        "underlying_price_usd": prices.get(res["underlying"]),
                                        "implied_apy": data["impliedApy"]})
        b.nodes[pid]["evidence"] += api_ev("pendle", f"prices_{chain_id}_" + "_".join(
            a.lower()[:10] for a in [pos["pt"], res["underlying"]]))
        info = {"market": m["address"], "venue": res["venue"], "underlying": res["underlying"],
                "pt_price_usd": pt_price, "expiry": m["expiry"], "liquidity_usd": data["liquidity"]["usd"]}
    elif pos["type"] == "aave_supply":
        info = resolve_aave_supply(b, pid, pos)
    elif pos["type"] == "morpho_supply":
        info = resolve_morpho_supply(b, pid, pos)
    elif pos["type"] == "hold":
        a_id = asset_node(b, pos["asset"], chain_id)
        b.add_edge(edge(a_id, pid, "backing", "documented", doc_ev("data/positions.json"),
                        {"why": "La position est l'actif lui-même"}))
        b.enqueue("asset", a_id, 1, address=pos["asset"], chain_id=chain_id, collateral=True)
        info = {"asset": pos["asset"]}
    return pid, info


def aave_pool_node(b, chain_id, pool, market):
    key = f"market_{chain_id}_{pool.lower()}"
    total = sum(float(r["size"]["usd"]) for r in market["reserves"])
    llama = S.llama_protocols().get("aave-v3", {})
    return b.add_node(node(f"lending:aave:{chain_id}:{pool.lower()}", "Aave V3 (pool Ethereum Core)", "lending",
                           address=pool, metrics={"total_supply_usd": total, "tvl_usd": llama.get("tvl"),
                                                  "ethereum_tvl_usd": llama.get("ethereum_tvl")},
                           evidence_list=api_ev("aave", key) + api_ev("defillama", "protocols_subset"),
                           details={"market_id": pool.lower(), "protocol": "aave-v3",
                                    "oracles_defillama": [o["name"] for o in llama.get("oraclesBreakdown", [])]}))


def _collateral_lt(r):
    lts = [float(r["supplyInfo"]["liquidationThreshold"]["value"])] if r["supplyInfo"]["canBeCollateral"] else []
    lts += [float(e["liquidationThreshold"]["value"]) for e in (r.get("eModeInfo") or []) if e.get("canBeCollateral")]
    return max(lts) if lts else 0.0


def resolve_aave_supply(b, pid, pos):
    chain_id, pool = pos["chain_id"], pos["pool"].lower()
    market = S.aave_market(chain_id, pool)
    key = f"market_{chain_id}_{pool}"
    reserve = next(r for r in market["reserves"] if r["underlyingToken"]["address"].lower() == pos["asset"].lower())
    pool_id = aave_pool_node(b, chain_id, pool, market)
    avail = float(((reserve.get("borrowInfo") or {}).get("availableLiquidity") or {}).get("usd") or 0)
    borrowed = float(((reserve.get("borrowInfo") or {}).get("total") or {}).get("usd") or 0)
    b.nodes[pid]["metrics"].update({"reserve_size_usd": float(reserve["size"]["usd"]), "reserve_borrowed_usd": borrowed,
                                    "available_liquidity_usd": avail})
    b.add_edge(edge(pool_id, pid, "backing", "api", api_ev("aave", key),
                    {"market_id": pool, "why": "Le prêteur détient des aTokens remboursés par le pool",
                     "available_liquidity_usd": avail}))
    a_id = asset_node(b, pos["asset"], chain_id)
    b.add_edge(edge(a_id, pid, "backing", "api", api_ev("aave", key), {"why": "On récupère l'actif prêté"}))
    b.enqueue("asset", a_id, 1, address=pos["asset"], chain_id=chain_id)
    b.enqueue("aave_pool", pool_id, 1, chain_id=chain_id, pool=pool, exclude=pos["asset"].lower())
    ch = "chain:ethereum"
    b.add_edge(edge(ch, pool_id, "settlement", "documented", doc_ev(DOCUMENTED["chains"]["ethereum"]["url"])))
    return {"pool": pool, "available_liquidity_usd": avail, "reserve_size_usd": float(reserve["size"]["usd"])}


def expand_aave_pool(b, pool_id, depth, chain_id, pool, exclude=None):
    """Collatéraux principaux du pool : s'ils s'effondrent, les prêteurs héritent d'une créance douteuse."""
    market = S.aave_market(chain_id, pool)
    key = f"market_{chain_id}_{pool}"
    coll = [(r, _collateral_lt(r)) for r in market["reserves"] if not r["isFrozen"]]
    coll = [(r, lt) for r, lt in coll if lt > 0 and r["underlyingToken"]["address"].lower() != exclude]
    total = sum(float(r["size"]["usd"]) for r, _ in coll) or 1.0
    coll.sort(key=lambda x: -float(x[0]["size"]["usd"]))
    for r, lt in coll[:TOP_POOL_COLLATERALS]:
        addr = r["underlyingToken"]["address"].lower()
        share = float(r["size"]["usd"]) / total
        a_id = asset_node(b, addr, chain_id)
        b.nodes[a_id]["metrics"]["aave_collateral_usd"] = float(r["size"]["usd"])
        b.add_edge(edge(a_id, pool_id, "bad_debt", "api", api_ev("aave", key),
                        {"market_id": pool, "share_of_collateral": share, "liquidation_threshold": lt,
                         "why": f"{r['underlyingToken']['symbol']} = {share:.0%} du collatéral du pool"}))
        b.enqueue("asset", a_id, depth + 1, address=addr, chain_id=chain_id)
        o = r["usdOracleAddress"].lower()
        o_id = resolve_oracle(b, o, depth + 1, label=f"Oracle Aave {r['underlyingToken']['symbol']}")
        sym = r["underlyingToken"]["symbol"].lower()
        desc = ((b.nodes.get(o_id) or {}).get("details") or {}).get("description", "") if o_id else ""
        if desc and sym not in desc.lower() and sym.lstrip("w") not in desc.lower():
            # A5 : l'actif est évalué avec le prix d'un AUTRE actif (ex. USDe évalué comme USDT)
            b.nodes[o_id].setdefault("flags", [])
            if "proxy_price" not in b.nodes[o_id]["flags"]:
                b.nodes[o_id]["flags"].append("proxy_price")
            b.nodes[o_id].setdefault("details", {}).setdefault("proxy_for", []).append(r["underlyingToken"]["symbol"])
        if o_id:
            b.add_edge(edge(o_id, pool_id, "pricing", "api", api_ev("aave", key),
                            {"market_id": pool, "asset": r["underlyingToken"]["symbol"],
                             "why": "Prix utilisé pour décider des liquidations"}))


def resolve_morpho_supply(b, pid, pos):
    chain_id, mid = pos["chain_id"], pos["market_id"].lower()
    mk = S.morpho_market(chain_id, mid)
    key = f"market_{chain_id}_{mid}"
    l_id = morpho_market_node(b, chain_id, mk, f"market_{chain_id}_{mid}")
    st = mk["state"]
    liquidity = float(st["supplyAssetsUsd"] or 0) - float(st["borrowAssetsUsd"] or 0)
    b.nodes[pid]["metrics"].update({"available_liquidity_usd": liquidity})
    b.add_edge(edge(l_id, pid, "backing", "api", api_ev("morpho", key),
                    {"market_id": mid, "why": "Le prêteur est remboursé par ce marché isolé"}))
    loan = mk["loanAsset"]["address"].lower()
    a_id = asset_node(b, loan, chain_id)
    b.add_edge(edge(a_id, pid, "backing", "api", api_ev("morpho", key), {"why": "On récupère l'actif prêté"}))
    b.enqueue("asset", a_id, 1, address=loan, chain_id=chain_id)
    coll = mk["collateralAsset"]["address"].lower()
    c_id = asset_node(b, coll, chain_id)
    b.add_edge(edge(c_id, l_id, "bad_debt", "api", api_ev("morpho", key),
                    {"market_id": mid, "share_of_collateral": 1.0, "lltv": int(mk["lltv"]) / 1e18,
                     "why": "Collatéral unique du marché : s'il chute plus vite que les liquidations, créance douteuse"}))
    if coll in {p.lower() for p in all_pts()}:
        res = expand_pendle_pt(b, c_id, coll, chain_id, 2)
        if res:
            b.nodes[c_id]["kind"] = "asset"
    else:
        b.enqueue("asset", c_id, 2, address=coll, chain_id=chain_id)
    o_id = resolve_oracle(b, mk["oracle"]["address"].lower(), 2, api_type=mk["oracle"].get("type"),
                          api_data=mk["oracle"].get("data"), label="Oracle du marché")
    if o_id:
        b.add_edge(edge(o_id, l_id, "pricing", "api", api_ev("morpho", key),
                        {"market_id": mid, "why": "Prix utilisé pour décider des liquidations"}))
    b.add_edge(edge("chain:ethereum", l_id, "settlement", "documented", doc_ev(DOCUMENTED["chains"]["ethereum"]["url"])))
    return {"market_id": mid, "available_liquidity_usd": liquidity}


def morpho_market_node(b, chain_id, mk, key):
    st = mk["state"]
    label = f"Morpho {mk['collateralAsset']['symbol']}/{mk['loanAsset']['symbol']} (LLTV {int(mk['lltv']) / 1e16:.1f} %)"
    llama = S.llama_protocols().get("morpho-blue", {})
    return b.add_node(node(f"lending:morpho:{chain_id}:{mk['marketId'].lower()}", label, "lending",
                           metrics={"collateral_usd": st["collateralAssetsUsd"], "borrow_usd": st["borrowAssetsUsd"],
                                    "supply_usd": st["supplyAssetsUsd"], "lltv": int(mk["lltv"]) / 1e18},
                           evidence_list=api_ev("morpho", key),
                           details={"market_id": mk["marketId"].lower(), "protocol": "morpho-blue",
                                    "protocol_tvl_usd": llama.get("tvl")}))


# ---------------------------------------------------------------------------
# Résolveur « collatéral » : qui accepte cet actif en garantie ?
# ---------------------------------------------------------------------------

def expand_collateral(b, asset_id, depth, address, chain_id, venue=None, venue_liquidity=None):
    a = address.lower()
    target = venue or asset_id
    markets = S.morpho_markets_by_collateral(chain_id, a)
    key = f"markets_collateral_{chain_id}_{a}"
    good = [m for m in markets if m["listed"] and float(m["state"]["collateralAssetsUsd"] or 0) >= MIN_COLLATERAL_USD]
    good.sort(key=lambda m: -float(m["state"]["collateralAssetsUsd"] or 0))
    for mk in good[:TOP_COLLATERAL_MARKETS]:
        l_id = morpho_market_node(b, chain_id, mk, key)
        coll = float(mk["state"]["collateralAssetsUsd"])
        pressure = coll / venue_liquidity if venue_liquidity else None
        why = "Des liquidations forceraient la vente de cet actif"
        if pressure:
            why += f" : {coll / 1e6:.1f} M$ de collatéral face à {venue_liquidity / 1e6:.1f} M$ de liquidité AMM"
        b.add_edge(edge(l_id, target, "collateral_liquidation", "api", api_ev("morpho", key),
                        {"market_id": mk["marketId"].lower(), "collateral_usd": coll,
                         "pressure_ratio": pressure, "why": why}))
        o_id = resolve_oracle(b, mk["oracle"]["address"].lower(), depth + 2, api_type=mk["oracle"].get("type"),
                              api_data=mk["oracle"].get("data"), label="Oracle du marché")
        if o_id:
            b.add_edge(edge(o_id, l_id, "pricing", "api", api_ev("morpho", key),
                            {"market_id": mk["marketId"].lower(), "why": "Prix utilisé pour décider des liquidations"}))
    # Aave : l'actif est-il une réserve du pool principal ?
    pool = "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2"
    market = S.aave_market(chain_id, pool)
    for r in market["reserves"]:
        if r["underlyingToken"]["address"].lower() == a and not r["isFrozen"] and _collateral_lt(r) > 0 \
                and float(r["size"]["usd"]) >= MIN_COLLATERAL_USD:
            pool_id = aave_pool_node(b, chain_id, pool, market)
            b.add_edge(edge(pool_id, target, "collateral_liquidation", "api", api_ev("aave", f"market_{chain_id}_{pool}"),
                            {"market_id": pool, "collateral_usd": float(r["size"]["usd"]),
                             "why": f"{r['underlyingToken']['symbol']} accepté en garantie sur Aave ({float(r['size']['usd']) / 1e6:.0f} M$)"}))


# ---------------------------------------------------------------------------
# Résolveur « oracle » : décodage on-chain
# ---------------------------------------------------------------------------

def _ok(hexdata):
    return bool(hexdata) and hexdata != "0x"


def resolve_oracle(b, address, depth, api_type=None, api_data=None, label="Oracle"):
    """Identifie un oracle en lisant le contrat ; renvoie l'id du nœud."""
    a = address.lower()
    oid = f"oracle:1:{a}"
    if oid in b.nodes:
        return oid
    code = S.eth_get_code(a)
    impl = S.minimal_proxy_target(code or "")
    flags, details, metrics = [], {"api_type": api_type}, {}
    ev = [{"source": "eth_getCode", "url": f"https://etherscan.io/address/{a}#code",
           "fetched_at": S.meta("rpc", f"code_{a}")["fetched_at"]}]
    if impl:
        details["proxy_implementation"] = impl
    primary = S.dec_address(call(a, "primaryOracle()"))
    backup = S.dec_address(call(a, "backupOracle()"))
    if primary and backup:
        thr = S.dec_uint(call(a, "deviationThreshold()"))
        ch = S.dec_uint(call(a, "challengeTimelockDuration()"))
        he = S.dec_uint(call(a, "healingTimelockDuration()"))
        cur = S.dec_address(call(a, "currentOracle()"))
        metrics.update({"deviation_threshold": thr / 1e18 if thr else None, "challenge_timelock_h": ch / 3600 if ch else None,
                        "healing_timelock_h": he / 3600 if he else None})
        details.update({"type": "meta_oracle", "using": "primaire" if cur == primary else "secours"})
        flags.append("meta_oracle_with_fallback")
        name = f"Méta-oracle {short(a)} (primaire + secours, bascule après {ch / 3600:.0f} h)" if ch else f"Méta-oracle {short(a)}"
        b.add_node(node(oid, name, "oracle", address=a, metrics=metrics, evidence_list=ev + chain_ev(a, "primaryOracle()"),
                        flags=flags, details=details))
        for role, sub in (("primaire", primary), ("secours", backup)):
            sid = resolve_oracle(b, sub, depth + 1, label=f"Oracle {role}")
            if sid:
                b.add_edge(edge(sid, oid, "pricing", "verified_onchain",
                                chain_ev(a, "primaryOracle()" if role == "primaire" else "backupOracle()"),
                                {"role": role, "why": f"Source de prix {role} du méta-oracle"}))
        return oid
    feeds = {}
    for fn in ("BASE_FEED_1()", "BASE_FEED_2()", "QUOTE_FEED_1()", "QUOTE_FEED_2()"):
        f = S.dec_address(call(a, fn))
        if f:
            feeds[fn] = f
    if not feeds and api_data:
        for k in ("baseFeedOne", "baseFeedTwo", "quoteFeedOne", "quoteFeedTwo"):
            if api_data.get(k):
                feeds[k] = api_data[k]["address"].lower()
    vault_api = ((api_data or {}).get("baseOracleVault") or {}).get("address")
    if not feeds and vault_api:
        # Oracle Morpho sans aucun flux de marché : prix = taux de change du coffre ERC-4626 seulement
        details.update({"type": "vault_rate_only", "base_vault": vault_api.lower()})
        flags += ["non_market_price", "single_source"]
        b.add_node(node(oid, f"{label} {short(a)} (taux du coffre {token_symbol(vault_api)} seulement, sans prix de marché)",
                        "oracle", address=a, evidence_list=ev + api_ev_oracle(), flags=flags, details=details))
        return oid
    if feeds:
        vault = S.dec_address(call(a, "BASE_VAULT()"))
        details.update({"type": "morpho_chainlink_v2", "base_vault": vault})
        b.add_node(node(oid, f"{label} {short(a)} (combinaison de flux)", "oracle", address=a,
                        evidence_list=ev + chain_ev(a, "BASE_FEED_1()"), details=details))
        real = 0
        for fn, f in feeds.items():
            fid = resolve_feed(b, f, depth + 1)
            if fid:
                real += 1
                b.add_edge(edge(fid, oid, "pricing", "verified_onchain", chain_ev(a, fn) if fn.endswith("()") else ev,
                                {"role": fn.strip("()"), "why": "Flux de prix combiné par l'oracle"}))
        if real <= 1:
            b.nodes[oid].setdefault("flags", []).append("single_source")
        return oid
    return resolve_feed(b, a, depth, label=label)


def api_ev_oracle():
    """Preuve générique : la structure de l'oracle vient de l'API Morpho (champ oracle.data)."""
    return [{"source": "morpho", "url": S.MORPHO_GQL, "fetched_at": next(
        (v["fetched_at"] for k, v in S.used_sources().items() if k[0] == "morpho"), "?")}]


def resolve_feed(b, address, depth, label="Flux de prix"):
    a = address.lower()
    fid = f"oracle:1:{a}"
    if fid in b.nodes:
        return fid
    desc = S.dec_string(call(a, "description()"))
    if desc and "dummy" in desc.lower():
        return None  # flux constant (= 1) : pas une dépendance
    flags, details, metrics = [], {}, {}
    ev = chain_ev(a, "description()")
    pt = S.dec_address(call(a, "PT()"))
    disc = S.dec_uint(call(a, "baseDiscountPerYear()"))
    if pt and disc is not None:
        details.update({"type": "pendle_linear_discount", "pt": pt})
        metrics["discount_per_year"] = disc / 1e18
        flags += ["non_market_price", "single_source"]
        name = f"Flux Pendle « décote linéaire » ({disc / 1e16:.1f} %/an, ne regarde pas le marché)"
        ev = chain_ev(a, "baseDiscountPerYear()")
    elif desc:
        agg = S.dec_address(call(a, "aggregator()"))
        rd = call(a, "latestRoundData()")
        answer = S.dec_uint(rd, 1)
        updated = S.dec_uint(rd, 3)
        dec = S.dec_uint(call(a, "decimals()"))
        details.update({"type": "chainlink_proxy" if agg else "price_feed", "description": desc, "aggregator": agg})
        if answer is not None and dec is not None:
            metrics.update({"answer": answer / 10 ** dec, "updated_at": updated})
        provider = "Chainlink" if agg else "fournisseur non identifié"
        low = desc.lower()
        for word in ("Chainlink", "Ojo", "RedStone", "Chronicle", "Pyth", "API3"):
            if not agg and word.lower() in low:
                provider = f"{word} (d'après description())"
        if "exchange rate" in low or "ratio" in low:
            flags.append("non_market_price")
            details["kind_of_price"] = "taux de change on-chain (pas un prix de marché)"
        doc = next((v for v in DOCUMENTED["oracle_providers"].values() if v["description"] == desc), None)
        if doc:
            metrics["operators"] = doc["operators"]
            metrics["deviation"] = doc["deviation"]
            ev = ev + doc_ev(doc["url"])
        if not agg:
            capo = S.dec_address(call(a, "ASSET_TO_USD_AGGREGATOR()")) or S.dec_address(call(a, "BASE_TO_USD_AGGREGATOR()"))
            ratio = S.dec_address(call(a, "RATIO_PROVIDER()"))
            if capo or ratio:
                details.update({"type": "aave_capo_adapter", "underlying_feed": capo, "ratio_provider": ratio})
                provider = "Chainlink (adaptateur CAPO d'Aave)"
                flags.append("rate_capped")
        name = f"{desc} ({provider})"
        details["provider"] = provider
    else:
        name = f"{label} {short(a)} (type non décodé)"
        flags.append("undecoded")
        ev = [{"source": "eth_getCode", "url": f"https://etherscan.io/address/{a}#code",
               "fetched_at": S.meta("rpc", f"code_{a}")["fetched_at"]}]
    b.add_node(node(fid, name, "oracle", address=a, metrics=metrics, evidence_list=ev, flags=flags, details=details))
    return fid


# ---------------------------------------------------------------------------
# Résolveurs « actif » et « émetteur »
# ---------------------------------------------------------------------------

def expand_asset(b, asset_id, depth, address, chain_id, collateral=False):
    a = address.lower()
    base = None
    for fn in ("asset()", "stETH()"):
        base = S.dec_address(call(a, fn))
        if base:
            base_id = asset_node(b, base, chain_id)
            b.add_edge(edge(base_id, asset_id, "backing", "verified_onchain", chain_ev(a, fn),
                            {"why": f"{token_symbol(a)} est une enveloppe de {token_symbol(base)} (lu on-chain : {fn})"}))
            b.enqueue("asset", base_id, depth, address=base, chain_id=chain_id)  # une enveloppe ne compte pas comme un étage
            break
    info = DOCUMENTED["tokens"].get(a)
    if info and info.get("issuer") and not base:
        iss = info["issuer"]
        meta = DOCUMENTED["issuers"][iss]
        llama = S.llama_protocols().get(meta.get("defillama", ""), {})
        metrics = {"tvl_usd": llama.get("tvl"), "ethereum_tvl_usd": llama.get("ethereum_tvl")}
        i_id = b.add_node(node(f"issuer:{iss}", meta["name"], "issuer", metrics=metrics,
                               evidence_list=doc_ev(meta["url"]) + (api_ev("defillama", "protocols_subset") if llama else []),
                               details={"category": llama.get("category")}))
        b.add_edge(edge(i_id, asset_id, "backing", "documented", doc_ev(info["url"]),
                        {"why": f"{meta['name']} émet et garantit {info['symbol']}"}))
        b.enqueue("issuer", i_id, depth + 1, issuer=iss, chain_id=chain_id)
    if collateral:  # seulement pour l'actif directement sous la position
        b.enqueue("collateral", asset_id, depth, address=a, chain_id=chain_id)


def expand_issuer(b, issuer_id, depth, issuer, chain_id):
    meta = DOCUMENTED["issuers"][issuer]
    if meta.get("hedging_from_api"):
        venues = S.ethena_collateral_by_exchange()
        for name, v in sorted(venues.items(), key=lambda kv: -kv[1]["share"]):
            if name == "Unallocated" or v["share"] < 0.001:
                continue
            vid = b.add_node(node(f"perp:{name.lower()}", f"{name} (bourse de couverture)", "perp_exchange",
                                  metrics={"ethena_collateral_usd": v["usd"], "ethena_share": v["share"]},
                                  evidence_list=api_ev("ethena", "positions_current_collateral")))
            b.add_edge(edge(vid, issuer_id, "hedging", "api", api_ev("ethena", "positions_current_collateral"),
                            {"share_of_backing": v["share"],
                             "why": f"{v['share']:.1%} du collatéral d'Ethena est alloué à {name} pour la couverture"}))
    dep = meta.get("deployments_from_api")
    if dep:
        cat = next((c for c in S.ethena_collateral_breakdown() if c["category"] == dep["category"]), None)
        if cat:
            share = cat["percentage"] / len(dep["targets"])  # HYPOTHÈSE : répartition égale (non publiée)
            for target in dep["targets"]:
                if target not in b.nodes:
                    continue  # on ne relie qu'aux nœuds déjà présents (évite d'élargir le graphe)
                b.add_edge(edge(target, issuer_id, "backing", "api", api_ev("ethena", "collateral_breakdown"),
                                {"market_id": target.split(":")[-1], "share_of_backing": share,
                                 "why": f"{cat['percentage']:.0%} du collatéral d'Ethena est placé en « {cat['category']} » "
                                        f"({cat['assets']}) ; HYPOTHÈSE : moitié chacun, soit {share:.0%} ici"}))
    for d in meta.get("deps", []):
        if "token" in d:
            t_id = asset_node(b, d["token"], chain_id)
            b.add_edge(edge(t_id, issuer_id, d["mechanism"], "documented", doc_ev(d["url"]), {"why": d["name"]}))
            b.enqueue("asset", t_id, depth + 1, address=d["token"], chain_id=chain_id)
            continue
        status = d.get("status", "documented")
        ev = api_ev("defillama", "protocols_subset") if status == "api" else doc_ev(d["url"])
        did = b.add_node(node(d["id"], d["name"], d["kind"], evidence_list=ev))
        b.add_edge(edge(did, issuer_id, d["mechanism"], status, ev, {"why": d.get("note", d["name"])}))


# ---------------------------------------------------------------------------
# Moteur
# ---------------------------------------------------------------------------

def discover(position_id):
    pos = next(p for p in POSITIONS["positions"] if p["id"] == position_id)
    b = Builder(f"position:{pos['id']}")
    pid, info = resolve_position(b, pos)
    while b.queue:
        kind, nid, depth, ctx = b.queue.popleft()
        if kind == "asset":
            expand_asset(b, nid, depth, ctx["address"], ctx["chain_id"], ctx.get("collateral", False))
        elif kind == "issuer":
            expand_issuer(b, nid, depth, ctx["issuer"], ctx["chain_id"])
        elif kind == "collateral":
            expand_collateral(b, nid, depth, ctx["address"], ctx["chain_id"], ctx.get("venue"), ctx.get("venue_liquidity"))
        elif kind == "aave_pool":
            expand_aave_pool(b, nid, depth, ctx["chain_id"], ctx["pool"], ctx.get("exclude"))
    # le nœud chaîne relie aussi les contrats et marchés (tout s'exécute sur Ethereum)
    for n in list(b.nodes.values()):
        if n["kind"] in ("protocol", "venue", "lending") and n["id"] != pid:
            b.add_edge(edge("chain:ethereum", n["id"], "settlement", "documented",
                            doc_ev(DOCUMENTED["chains"]["ethereum"]["url"])))
    # on retire les nœuds qui n'atteignent pas la position (ne devrait pas arriver)
    graph = {"position_id": pos["id"], "position": {**pos, "id": pid, "config_id": pos["id"], "info": info},
             "nodes": list(b.nodes.values()), "edges": b.edges, "loops": b.loops,
             "meta": {"generated_at": S.now_iso(), "offline": S.is_offline(), "max_depth_limit": MAX_DEPTH,
                      "sources": list(S.used_sources().values())}}
    graph["checks"] = {"errors": validate(graph), "coverage": coverage(graph)}
    return graph


def main(argv):
    offline = "--offline" in argv
    args = [a for a in argv if not a.startswith("--")]
    S.set_offline(offline)
    ids = args or [p["id"] for p in POSITIONS["positions"] if p.get("main")]
    for pid in ids:
        g = discover(pid)
        out = ROOT / "data" / "graphs" / f"{pid}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(g, indent=1, ensure_ascii=False), encoding="utf-8")
        cov = g["checks"]["coverage"]
        print(f"{pid}: {cov['dependencies']} dépendances, profondeur {cov['max_depth']}, "
              f"{cov['verified_share']:.0%} non supposées, erreurs: {g['checks']['errors'] or 'aucune'}")
        for e in g["edges"]:
            print(f"   {e['from']:55s} → {e['to']:45s} {e['mechanism']:22s} {e['status']}")
        for lp in g["loops"]:
            print(f"   BOUCLE {lp['from']} → {lp['to']} ({lp['mechanism']})")


if __name__ == "__main__":
    main(sys.argv[1:])
