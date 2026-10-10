"""Collecte un instantané vérifiable pour le stress test Morpho/PT-sUSDe.

Commande : python tools/collect_cascade.py
Chaque valeur provient d'une API ou d'un eth_call frais ; aucune donnée manquante
n'est remplacée par une estimation. L'ancien instantané reste intact en cas d'échec.
"""

import json
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from risk_engine import sources as S  # noqa: E402
from risk_engine.keccak import selector  # noqa: E402

POSITION_ID = "morpho-ptsusde-usdc"
PT = "0xb195b618ea52b77cb2a58846f452f59f8dfa9390"
SUSDE = "0x9d39a5de30e57443bff2a8307a4256c8797a3497"
SIZES_PT = (250_000, 500_000, 1_000_000, 1_500_000, 2_000_000, 2_500_000,
            3_000_000, 4_000_000, 4_500_000, 5_000_000)
OUT = ROOT / "data" / "cascade" / f"{POSITION_ID}.json"


def graphql(query):
    """Interroge Morpho et rejette explicitement les erreurs GraphQL."""
    result = S._post_json(S.MORPHO_GQL, {"query": query})
    if result.get("errors"):
        raise RuntimeError(f"Morpho GraphQL : {result['errors']}")
    return result["data"]


def rpc(method, params):
    """Lit Ethereum via le RPC public ; échoue si la réponse ne contient pas de résultat."""
    result = S._post_json(S.RPC_URL, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if "error" in result or "result" not in result:
        raise RuntimeError(f"RPC {method} : {result.get('error', result)}")
    return result["result"]


def onchain_address(contract, signature, block):
    """Décode une adresse renvoyée par une fonction Solidity sans argument."""
    raw = rpc("eth_call", [{"to": contract, "data": selector(signature)}, block])
    if len(raw) < 42:
        raise ValueError(f"Adresse manquante pour {signature}")
    return "0x" + raw[-40:].lower()


def quote(pt, susde, size):
    """Cotation PT→sUSDe de taille exacte ; un refus est conservé comme tel."""
    url = f"{S.PENDLE}/v3/sdk/1/convert"
    body = {"receiver": "0x000000000000000000000000000000000000dEaD",
            "slippage": 0.05, "enableAggregator": False, "useLimitOrder": False,
            "inputs": [{"token": pt, "amount": str(size * 10**18)}], "outputs": [susde]}
    try:
        route = S._post_json(url, body)["routes"][0]
        if route["outputs"][0]["token"].lower() != susde.lower():
            raise ValueError("Pendle a coté un token de sortie inattendu")
        return {"size_pt": size, "ok": True, "out_susde_raw": route["outputs"][0]["amount"],
                "price_impact": route["data"]["priceImpact"], "fee_usd": route["data"]["fee"]["usd"],
                "url": url, "request": body, "fetched_at": S.now_iso()}
    except urllib.error.HTTPError as exc:
        if exc.code != 400:
            raise
        return {"size_pt": size, "ok": False, "status": 400,
                "reason": exc.read().decode("utf-8", "replace")[:240], "url": url, "request": body,
                "fetched_at": S.now_iso()}


def collect():
    """Assemble positions, état du marché, oracle on-chain et courbe de vente datés."""
    positions = json.loads((ROOT / "data" / "positions.json").read_text())["positions"]
    position = next(p for p in positions if p["id"] == POSITION_ID)
    mid = position["market_id"].lower()
    market_query = ('{ marketById(marketId:"%s", chainId:1) { marketId lltv '
                    'loanAsset { address symbol decimals } collateralAsset { address symbol decimals } '
                    'oracle { address } state { supplyAssets borrowAssets collateralAssets } } }' % mid)
    market = graphql(market_query)["marketById"]
    if not market or market["marketId"].lower() != mid:
        raise ValueError("Le marché attendu est introuvable")
    if market["collateralAsset"]["address"].lower() != PT or market["loanAsset"]["symbol"] != "USDC":
        raise ValueError("Les tokens du marché ne correspondent pas à la configuration")

    borrowers = []
    offset = 0
    while True:
        q = ('{ marketPositions(first:100, skip:%d, orderBy:BorrowShares, orderDirection:Desc, '
             'where:{marketUniqueKey_in:["%s"]}) { items { user { address } '
             'state { borrowAssets collateral } } } }' % (offset, mid))
        items = graphql(q)["marketPositions"]["items"]
        borrowers.extend({"address": x["user"]["address"].lower(),
                          "debt_raw": x["state"]["borrowAssets"],
                          "collateral_raw": x["state"]["collateral"]} for x in items)
        if len(items) < 100:
            break
        offset += len(items)
    if len({x["address"] for x in borrowers}) != len(borrowers):
        raise ValueError("Emprunteur dupliqué dans la pagination")
    total_debt = sum(int(x["debt_raw"]) for x in borrowers)
    total_collateral = sum(int(x["collateral_raw"]) for x in borrowers)
    market_debt = int(market["state"]["borrowAssets"])
    market_collateral = int(market["state"]["collateralAssets"])
    if abs(total_debt - market_debt) > max(100, market_debt // 1000):
        raise ValueError("La somme des dettes ne correspond pas au marché (collecte désynchronisée)")
    if abs(total_collateral - market_collateral) > max(100, market_collateral // 1000):
        raise ValueError("La somme des garanties ne correspond pas au marché")

    block = rpc("eth_blockNumber", [])
    oracle_address = market["oracle"]["address"].lower()
    raw_price = int(rpc("eth_call", [{"to": oracle_address, "data": selector("price()")}, block]), 16)
    primary_oracle = onchain_address(oracle_address, "primaryOracle()", block)
    backup_oracle = onchain_address(oracle_address, "backupOracle()", block)
    current_oracle = onchain_address(oracle_address, "currentOracle()", block)
    if current_oracle not in (primary_oracle, backup_oracle):
        raise ValueError("L'oracle courant ne correspond ni au primaire ni au secours")
    loan_decimals = int(market["loanAsset"]["decimals"])
    collateral_decimals = int(market["collateralAsset"]["decimals"])
    oracle_price = raw_price * 10**collateral_decimals / (10**36 * 10**loan_decimals)
    prices_url = f"{S.PENDLE}/v1/1/assets/prices?addresses={PT},{SUSDE}"
    prices = S._get_json(prices_url)["prices"]
    susde_usd = float(prices[SUSDE])
    quotes = [quote(PT, SUSDE, size) for size in SIZES_PT]
    if sum(x["ok"] for x in quotes) < 3:
        raise ValueError("Trop peu de cotations Pendle pour former une courbe")
    for x in quotes:
        if x["ok"]:
            x["proceeds_usdc_assumed"] = int(x["out_susde_raw"]) / 1e18 * susde_usd

    return {"schema_version": 1, "collected_at": S.now_iso(), "position_id": POSITION_ID,
            "market_id": mid, "market": market, "borrowers": borrowers,
            "coverage": {"positions": len(borrowers), "sum_debt_raw": str(total_debt),
                         "market_debt_raw": str(market_debt), "sum_collateral_raw": str(total_collateral),
                         "market_collateral_raw": str(market_collateral)},
            "oracle": {"address": oracle_address, "block": int(block, 16), "raw_price": str(raw_price),
                       "primary_oracle": primary_oracle, "backup_oracle": backup_oracle,
                       "current_oracle": current_oracle, "current_is_primary": current_oracle == primary_oracle,
                       "price_usdc_per_pt": oracle_price, "fetched_at": S.now_iso(), "rpc_url": S.RPC_URL},
            "pendle": {"pt": PT, "output_token": SUSDE, "susde_usd": susde_usd,
                       "pt_usd_reference": float(prices[PT]), "prices_url": prices_url,
                       "prices_fetched_at": S.now_iso(), "quotes": quotes,
                       "quote_endpoint_note": "POST /v3/sdk/1/convert, useLimitOrder=false : devis AMM sans carnet d'ordres limites."},
            "source_urls": {"morpho": S.MORPHO_GQL, "ethereum_rpc": S.RPC_URL,
                            "pendle_api": S.PENDLE}}


def save(snapshot):
    """Écrit atomiquement le relevé frais produit par `collect`."""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUT)


if __name__ == "__main__":
    snapshot = collect()
    save(snapshot)
    print(f"{OUT.relative_to(ROOT)} : {snapshot['coverage']['positions']} positions, "
          f"{sum(q['ok'] for q in snapshot['pendle']['quotes'])} cotations, "
          f"bloc {snapshot['oracle']['block']}, relevé {snapshot['collected_at']}")
