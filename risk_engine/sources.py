"""Accès aux données réelles, avec cache disque (bibliothèque standard uniquement).

Chaque réponse brute est enregistrée dans data/cache/<source>/<clé>.json avec l'URL,
la requête et l'heure du relevé (fetched_at). En mode hors ligne (--offline), seules
les réponses en cache sont relues : la démo fonctionne sans réseau.

Sources : API Pendle, API GraphQL Morpho, API GraphQL Aave, DeFiLlama, API publique
d'Ethena, et un nœud RPC Ethereum public (appels eth_call en lecture seule).
"""

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache"
RPC_URL = "https://ethereum-rpc.publicnode.com"
PENDLE = "https://api-v2.pendle.finance/core"
MORPHO_GQL = "https://api.morpho.org/graphql"
AAVE_GQL = "https://api.v3.aave.com/graphql"
LLAMA = "https://api.llama.fi"
ETHENA = "https://app.ethena.fi/api"
USER_AGENT = "Mozilla/5.0 (BelemRiskPrototype; +https://github.com/noxhrhl/HackathonBSANumberOne)"

_state = {"offline": False, "used": {}, "collect": None}


class MissingCache(RuntimeError):
    """Réponse absente du cache en mode hors ligne."""


def set_offline(flag: bool):
    _state["offline"] = bool(flag)


def start_collect():
    """Mode outil : en hors ligne, enregistre les requêtes absentes du cache au lieu d'échouer."""
    _state["collect"] = []
    return _state["collect"]


def write_cache_entry(source, key, url, request, response, fetched_at):
    """Écrit une entrée de cache relevée par un autre canal (même format que `cached`)."""
    path = cache_path(source, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"source": source, "key": key, "url": url, "request": request,
               "fetched_at": fetched_at, "response": response}
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")


def is_offline() -> bool:
    return _state["offline"]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:150]


def cache_path(source: str, key: str) -> Path:
    return CACHE_DIR / source / f"{_safe(key)}.json"


def used_sources() -> dict:
    """Toutes les entrées de cache lues pendant ce calcul (pour la liste des preuves)."""
    return dict(_state["used"])


def cached(source: str, key: str, url: str, fetch, request=None, transform=None):
    """Renvoie la réponse (depuis le cache hors ligne, sinon depuis le réseau puis cache).

    transform : réduit la réponse avant enregistrement (on ne garde que les champs utiles,
    pour un dépôt léger). La même réduction s'applique en ligne et dans le cache.
    """
    path = cache_path(source, key)
    if _state["offline"]:
        if not path.exists():
            if _state["collect"] is not None:  # mode « collecte » : on note la requête manquante
                _state["collect"].append({"source": source, "key": key, "url": url, "request": request})
                if source == "rpc":
                    return {"result": None}
            raise MissingCache(f"Absent du cache : {path.relative_to(ROOT)}")
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        try:
            response = fetch()
            if transform:
                response = transform(response)
            payload = {"source": source, "key": key, "url": url, "request": request,
                       "fetched_at": now_iso(), "response": response}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            if not path.exists():
                raise
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["stale_reason"] = str(exc)
    _state["used"][(source, key)] = {"source": source, "url": payload.get("url", url),
                                     "fetched_at": payload.get("fetched_at", "?")}
    return payload["response"]


def meta(source: str, key: str) -> dict:
    """{source, url, fetched_at} d'une entrée déjà lue — c'est la « preuve » d'une arête."""
    return _state["used"].get((source, key)) or {"source": source, "url": "", "fetched_at": "?"}


# ---------------------------------------------------------------------------
# HTTP brut
# ---------------------------------------------------------------------------

def _get_json(url: str, timeout: int = 40):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post_json(url: str, body: dict, timeout: int = 40):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def strip_chain(addr: str) -> str:
    """Pendle écrit '1-0xabc…' : on garde '0xabc…' en minuscules."""
    return addr.split("-", 1)[-1].lower()


# ---------------------------------------------------------------------------
# Pendle
# ---------------------------------------------------------------------------

def pendle_markets(chain_id: int, keep_pts: list) -> list:
    """Marchés Pendle actifs, réduits à ceux dont le PT nous intéresse."""
    keep = {a.lower() for a in keep_pts}
    url = f"{PENDLE}/v1/{chain_id}/markets/active"

    def transform(resp):
        return {"markets": [m for m in resp["markets"] if strip_chain(m["pt"]) in keep],
                "_filtre": "marchés dont le PT figure dans positions.json"}

    key = f"markets_active_{chain_id}_" + "_".join(sorted(a[:10] for a in keep))
    return cached("pendle", key, url, lambda: _get_json(url), transform=transform)["markets"]


def pendle_market_data(chain_id: int, market: str) -> dict:
    url = f"{PENDLE}/v2/{chain_id}/markets/{market.lower()}/data"

    def transform(resp):
        resp = dict(resp)
        resp.pop("estimatedDailyPoolRewards", None)
        return resp

    return cached("pendle", f"market_data_{chain_id}_{market.lower()}", url,
                  lambda: _get_json(url), transform=transform)


def pendle_prices(chain_id: int, addresses: list) -> dict:
    addrs = ",".join(a.lower() for a in addresses)
    url = f"{PENDLE}/v1/{chain_id}/assets/prices?addresses={addrs}"
    key = f"prices_{chain_id}_" + "_".join(a.lower()[:10] for a in addresses)
    return cached("pendle", key, url, lambda: _get_json(url))["prices"]


def pendle_quote(chain_id: int, token_in: str, token_out: str, amount_units: int) -> dict:
    """Cotation réelle de vente (API « convert » de Pendle) : impact de prix et frais.

    amount_units : quantité de tokens entiers (18 décimales). Renvoie
    {ok, out_amount, priceImpact, fee_usd} ou {ok: False, error} si le marché refuse.
    """
    wei = int(amount_units) * 10 ** 18
    url = (f"{PENDLE}/v2/sdk/{chain_id}/convert?receiver=0x000000000000000000000000000000000000dEaD"
           f"&slippage=0.05&tokensIn={token_in.lower()}&amountsIn={wei}&tokensOut={token_out.lower()}"
           f"&enableAggregator=false")

    def fetch():
        try:
            return _get_json(url)
        except urllib.error.HTTPError as exc:  # 400 = marché trop petit pour cette taille
            return {"_error": exc.read().decode("utf-8", "replace")[:300], "_status": exc.code}

    def transform(resp):
        if "_error" in resp:
            return {"ok": False, "error": resp["_error"], "status": resp.get("_status")}
        route = resp["routes"][0]
        return {"ok": True, "out_amount": route["outputs"][0]["amount"],
                "priceImpact": route["data"]["priceImpact"], "fee_usd": route["data"]["fee"]["usd"]}

    key = f"quote_{chain_id}_{token_in.lower()[:10]}_{token_out.lower()[:10]}_{int(amount_units)}"
    return cached("pendle", key, url, fetch, transform=transform)


# ---------------------------------------------------------------------------
# Morpho (GraphQL)
# ---------------------------------------------------------------------------

MORPHO_ORACLE_FIELDS = (
    "oracle { address type data { "
    "... on MorphoChainlinkOracleV2Data { baseFeedOne { address } baseFeedTwo { address } "
    "quoteFeedOne { address } quoteFeedTwo { address } baseOracleVault { address } "
    "quoteOracleVault { address } } "
    "... on MorphoChainlinkOracleData { baseFeedOne { address } baseFeedTwo { address } "
    "quoteFeedOne { address } quoteFeedTwo { address } } } }"
)


def morpho_markets_by_collateral(chain_id: int, collateral: str) -> list:
    """Marchés Morpho dont le collatéral est `collateral` (preuve qu'un marché réel existe)."""
    query = ("{ markets(first: 100, where: {chainId_in: [%d], collateralAssetAddress_in: [\"%s\"]}) "
             "{ items { marketId lltv listed loanAsset { symbol address } "
             "collateralAsset { symbol address } %s "
             "state { supplyAssetsUsd borrowAssetsUsd collateralAssetsUsd } } } }"
             % (chain_id, collateral.lower(), MORPHO_ORACLE_FIELDS))
    def transform(resp):
        items = resp["data"]["markets"]["items"]
        keep = [m for m in items if m["listed"] and float(m["state"]["collateralAssetsUsd"] or 0) >= 1e6]
        return {"data": {"markets": {"items": keep}}, "_filtre": "marchés listés avec au moins 1 M$ de collatéral"}

    resp = cached("morpho", f"markets_collateral_{chain_id}_{collateral.lower()}", MORPHO_GQL,
                  lambda: _post_json(MORPHO_GQL, {"query": query}), request={"query": query},
                  transform=transform)
    return resp["data"]["markets"]["items"]


def morpho_market(chain_id: int, market_id: str) -> dict:
    query = ("{ markets(first: 1, where: {chainId_in: [%d], uniqueKey_in: [\"%s\"]}) "
             "{ items { marketId lltv listed loanAsset { symbol address } "
             "collateralAsset { symbol address } %s "
             "state { supplyAssetsUsd borrowAssetsUsd collateralAssetsUsd } } } }"
             % (chain_id, market_id.lower(), MORPHO_ORACLE_FIELDS))
    resp = cached("morpho", f"market_{chain_id}_{market_id.lower()}", MORPHO_GQL,
                  lambda: _post_json(MORPHO_GQL, {"query": query}), request={"query": query})
    items = resp["data"]["markets"]["items"]
    if not items:
        raise ValueError(f"Marché Morpho introuvable : {market_id}")
    return items[0]


# ---------------------------------------------------------------------------
# Aave V3 (API GraphQL officielle)
# ---------------------------------------------------------------------------

def aave_market(chain_id: int, market_address: str) -> dict:
    query = ("{ market(request: {address: \"%s\", chainId: %d}) { name address reserves { "
             "underlyingToken { symbol address } size { usd } usdOracleAddress isFrozen isPaused "
             "supplyInfo { canBeCollateral liquidationThreshold { value } } "
             "borrowInfo { total { usd } availableLiquidity { usd } } "
             "eModeInfo { categoryId canBeCollateral liquidationThreshold { value } } } } }"
             % (market_address, chain_id))
    def transform(resp):
        m = resp["data"]["market"]
        keep = []
        for r in m["reserves"]:
            if r["isFrozen"] or float(r["size"]["usd"]) < 1e6:
                continue
            em = [e for e in (r.get("eModeInfo") or []) if e["canBeCollateral"] and float(e["liquidationThreshold"]["value"]) > 0]
            em = [max(em, key=lambda e: float(e["liquidationThreshold"]["value"]))] if em else []
            bi = r.get("borrowInfo")
            keep.append({"underlyingToken": r["underlyingToken"], "size": {"usd": r["size"]["usd"]},
                         "usdOracleAddress": r["usdOracleAddress"], "isFrozen": r["isFrozen"],
                         "supplyInfo": r["supplyInfo"], "eModeInfo": em,
                         "borrowInfo": {"total": bi["total"], "availableLiquidity": bi["availableLiquidity"]} if bi else None})
        return {"data": {"market": {"name": m["name"], "address": m["address"], "reserves": keep}},
                "_toutes_les_reserves": [[r["underlyingToken"]["symbol"], r["underlyingToken"]["address"].lower(), r["isFrozen"]]
                                         for r in m["reserves"]],
                "_filtre": "réserves non gelées d'au moins 1 M$ ; e-mode réduit au seuil de liquidation le plus haut"}

    resp = cached("aave", f"market_{chain_id}_{market_address.lower()}", AAVE_GQL,
                  lambda: _post_json(AAVE_GQL, {"query": query}), request={"query": query}, transform=transform)
    return resp["data"]["market"]


# ---------------------------------------------------------------------------
# DeFiLlama
# ---------------------------------------------------------------------------

LLAMA_SLUGS = ["ethena-usde", "pendle-v2", "morpho-blue", "aave-v3", "lido", "sky-lending"]


def llama_protocols() -> dict:
    """Métadonnées DeFiLlama (catégorie, TVL, TVL sur Ethereum, oracles) des protocoles utilisés."""
    url = f"{LLAMA}/protocols"

    def transform(resp):
        keep = {}
        for p in resp:
            if p.get("slug") in LLAMA_SLUGS:
                keep[p["slug"]] = {
                    "name": p.get("name"), "slug": p.get("slug"), "category": p.get("category"),
                    "parentProtocol": p.get("parentProtocol"), "tvl": p.get("tvl"),
                    "ethereum_tvl": (p.get("chainTvls") or {}).get("Ethereum"),
                    "oraclesBreakdown": p.get("oraclesBreakdown") or [],
                }
        return {"protocols": keep, "_filtre": "slugs utilisés par le projet (LLAMA_SLUGS)"}

    return cached("defillama", "protocols_subset", url, lambda: _get_json(url, 90),
                  transform=transform)["protocols"]


def llama_chain_tvl(chain_name: str = "Ethereum") -> float:
    url = f"{LLAMA}/v2/chains"

    def transform(resp):
        return {"chains": [c for c in resp if c.get("name") == chain_name]}

    chains = cached("defillama", f"chains_{chain_name}", url, lambda: _get_json(url),
                    transform=transform)["chains"]
    return float(chains[0]["tvl"]) if chains else 0.0


# ---------------------------------------------------------------------------
# Ethena (API publique de la page « Transparency »)
# ---------------------------------------------------------------------------

def ethena_collateral_by_exchange() -> dict:
    """Part du collatéral d'Ethena par bourse (couverture) — données Ethena."""
    url = f"{ETHENA}/positions/current/collateral"

    def transform(resp):
        rows = [r for r in resp["collateral"] if r.get("usdAmount", 0) > 0]
        return {"collateral": rows}

    rows = cached("ethena", "positions_current_collateral", url, lambda: _get_json(url),
                  transform=transform)["collateral"]
    by = {}
    for r in rows:
        by[r["exchange"]] = by.get(r["exchange"], 0.0) + float(r["usdAmount"])
    total = sum(by.values()) or 1.0
    return {k: {"usd": v, "share": v / total} for k, v in by.items()}


def ethena_collateral_breakdown() -> list:
    url = f"{ETHENA}/collateral-breakdown"
    return cached("ethena", "collateral_breakdown", url, lambda: _get_json(url))["breakdown"]


# ---------------------------------------------------------------------------
# RPC Ethereum (lecture seule)
# ---------------------------------------------------------------------------

def _rpc(method: str, params: list):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    resp = _post_json(RPC_URL, body)
    if "error" in resp:
        return {"error": resp["error"].get("message", "error")}
    return {"result": resp["result"]}


def eth_call(to: str, data: str):
    """Appel en lecture seule. Renvoie la chaîne hexadécimale, ou None si le contrat refuse."""
    to, data = to.lower(), data.lower()
    key = f"call_{to}_{data}"
    if len(data) > 74:  # plusieurs arguments : la clé tronquée à 150 caractères ne les distinguerait plus
        import hashlib
        key = f"call_{to}_{data[:10]}_{hashlib.sha1(data.encode()).hexdigest()[:16]}"
    out = cached("rpc", key, RPC_URL,
                 lambda: _rpc("eth_call", [{"to": to, "data": data}, "latest"]),
                 request={"method": "eth_call", "to": to, "data": data})
    return out.get("result")


def eth_get_code(address: str):
    a = address.lower()
    def transform(resp):  # on ne garde le code entier que pour les clones EIP-1167 (45 octets)
        code = resp.get("result") or "0x"
        return {"result": code if len(code) <= 92 else None, "code_size": (len(code) - 2) // 2, "prefix": code[:24]}

    out = cached("rpc", f"code_{a}", RPC_URL, lambda: _rpc("eth_getCode", [a, "latest"]),
                 request={"method": "eth_getCode", "address": a}, transform=transform)
    return out.get("result")


def cache_first(fn, *args):
    """Instantané pour la simulation : relit le cache s'il existe, sinon interroge la source
    (sauf en mode hors ligne, où l'absence lève MissingCache). Évite de rafraîchir, en
    passant, des réponses déjà utilisées comme preuves par les graphes."""
    prev = _state["offline"]
    _state["offline"] = True
    try:
        return fn(*args)
    except MissingCache:
        if prev:
            raise
        _state["offline"] = False
        return fn(*args)
    finally:
        _state["offline"] = prev


PENDLE_ROUTER = "0x888888888889758f76e7103c6cbf23abbf58f946"


def _arg_address(addr: str) -> str:
    return addr.lower().replace("0x", "").rjust(64, "0")


def _int256(word_hex: str) -> int:
    v = int(word_hex, 16)
    return v - (1 << 256) if v >= 1 << 255 else v


def pendle_market_state(market: str) -> dict:
    """État complet de l'AMM Pendle V2 : readState(router) (lecture seule, on-chain)."""
    from .keccak import selector
    out = eth_call(market, selector("readState(address)") + _arg_address(PENDLE_ROUTER))
    if not out or len(out) < 2 + 64 * 9:
        return None
    w = [out[2 + 64 * i: 2 + 64 * (i + 1)] for i in range(9)]
    return {"total_pt": _int256(w[0]) / 1e18, "total_sy": _int256(w[1]) / 1e18, "total_lp": _int256(w[2]) / 1e18,
            "scalar_root": _int256(w[4]) / 1e18, "expiry_ts": int(w[5], 16), "ln_fee_rate_root": int(w[6], 16) / 1e18,
            "reserve_fee_percent": int(w[7], 16), "last_ln_implied_rate": int(w[8], 16) / 1e18}


PENDLE_ROUTER_STATIC = "0x263833d47ea3fa4a30f269323aba6a107f9eb14c"


def pendle_static_sell_pt(market: str, pt_units: int):
    """Cotation on-chain AMM seul (RouterStatic.swapExactPtForSyStatic) : SY reçus, ou None si refus."""
    from .keccak import selector
    data = (selector("swapExactPtForSyStatic(address,uint256)") + _arg_address(market)
            + hex(int(pt_units) * 10 ** 18)[2:].rjust(64, "0"))
    v = dec_uint(eth_call(PENDLE_ROUTER_STATIC, data))
    return v / 1e18 if v is not None else None


def sy_exchange_rate(sy: str):
    """Taux SY → actif comptable (SY.exchangeRate(), 18 décimales)."""
    from .keccak import selector
    v = dec_uint(eth_call(sy, selector("exchangeRate()")))
    return v / 1e18 if v is not None else None


def oracle_price_raw(oracle: str):
    """IOracle.price() d'un oracle Morpho : prix du collatéral en unités de dette × 1e36 (brut)."""
    from .keccak import selector
    return dec_uint(eth_call(oracle, selector("price()")))


def erc20_decimals(token: str):
    from .keccak import selector
    return dec_uint(eth_call(token, selector("decimals()")))


# Décodage ABI minimal
def dec_address(hexdata):
    if not hexdata or len(hexdata) < 66:
        return None
    a = "0x" + hexdata[-40:]
    return None if int(a, 16) == 0 else a.lower()


def dec_uint(hexdata, word=0):
    if not hexdata or hexdata == "0x":
        return None
    h = hexdata[2:]
    chunk = h[64 * word:64 * (word + 1)]
    return int(chunk, 16) if chunk else None


def dec_string(hexdata):
    if not hexdata or len(hexdata) < 130:
        return None
    h = hexdata[2:]
    try:
        length = int(h[64:128], 16)
        return bytes.fromhex(h[128:128 + 2 * length]).decode("utf-8", "replace")
    except ValueError:
        return None


def dec_addresses(hexdata, n):
    if not hexdata:
        return []
    h = hexdata[2:]
    return [("0x" + h[64 * i + 24:64 * (i + 1)]).lower() for i in range(n)]


def minimal_proxy_target(code: str):
    """EIP-1167 : un clone de 45 octets qui délègue tout à une implémentation."""
    if code and code.startswith("0x363d3d373d3d3d363d73") and len(code) == 92:
        return ("0x" + code[22:62]).lower()
    return None
