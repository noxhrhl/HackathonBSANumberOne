"""Contrat de données du graphe de dépendances (une seule définition pour tout le projet).

Un graphe décrit UNE position et tout ce dont elle dépend.

Convention de direction : une arête « A → B » signifie « si A défaille, B est touché ».
La position est l'unique puits (aucune arête ne part d'elle).

Nœud : {id, name, kind, chain, address?, metrics:{...}, evidence:[...], flags?:[...], details?:{...}}
Arête : {from, to, mechanism, evidence:[...], status, details?:{...}, w?}
Preuve (evidence) : {source, url, fetched_at}
"""

from collections import deque

# Types de nœuds : une phrase chacun (exemples tirés du cas PT-sUSDe).
KINDS = {
    "position": "La ligne du portefeuille (ex. 2 M$ de PT-sUSDe-26NOV2026).",
    "chain": "Blockchain de règlement (ex. Ethereum) : si elle s'arrête, plus aucune transaction.",
    "asset": "Jeton dont la valeur sous-tend la position (ex. sUSDe, USDe).",
    "issuer": "Entité qui émet / garantit un actif (ex. Ethena pour USDe).",
    "oracle": "Source de prix utilisée par un protocole (ex. flux Chainlink USDe/USD).",
    "venue": "Lieu d'échange où l'on peut vendre (ex. AMM Pendle du marché PT-sUSDe).",
    "lending": "Marché de prêt qui accepte un actif en garantie (ex. Morpho PT-sUSDe/USDC).",
    "perp_exchange": "Bourse de dérivés où un émetteur couvre son risque (ex. Binance pour Ethena).",
    "custodian": "Dépositaire qui garde les actifs d'un émetteur (ex. Copper, Ceffu pour Ethena).",
    "bridge": "Pont inter-chaînes qui garantit un actif « emballé ».",
    "admin": "Clé d'administration / gouvernance capable de changer un contrat.",
    "protocol": "Code d'un protocole dont dépend le rachat (ex. contrat SY de Pendle).",
}

# Mécanismes : COMMENT le dommage voyage le long d'une arête.
MECHANISMS = {
    "backing": "Valeur du sous-jacent : si l'actif perd x %, ce qui en dépend perd ~x % (sUSDe → PT).",
    "pricing": "Un flux de prix utilisé par quelqu'un : un mauvais prix déclenche de mauvaises liquidations.",
    "exit_liquidity": "Capacité à vendre : un AMM dégradé rend la sortie chère ou impossible.",
    "collateral_liquidation": "Ventes forcées par un marché de prêt : les liquidations font baisser le prix.",
    "settlement": "Arrêt de la chaîne : impossible d'agir (sortir, rembourser) pendant l'arrêt.",
    "hedging": "Couverture de l'émetteur sur une bourse : si la bourse fait défaut, la couverture est perdue.",
    "custody": "Garde des actifs : si le dépositaire fait défaut, les actifs de l'émetteur sont touchés.",
    "bad_debt": "Créance irrécouvrable : un collatéral qui s'effondre laisse une perte aux prêteurs.",
}

# Statuts de vérification, du plus fort au plus faible.
STATUSES = {
    "verified_onchain": "Lu directement dans un contrat Ethereum (appel eth_call).",
    "api": "Fourni par l'API officielle du protocole ou d'un agrégateur (Pendle, Morpho, Aave, DeFiLlama, Ethena).",
    "documented": "Écrit dans la documentation officielle (lien fourni).",
    "assumed": "Hypothèse non vérifiée : affichée en pointillés rouges « À VÉRIFIER ».",
}
STATUS_ORDER = ["verified_onchain", "api", "documented", "assumed"]


def evidence(source: str, url: str, fetched_at: str) -> dict:
    return {"source": source, "url": url, "fetched_at": fetched_at}


def node(id, name, kind, chain="ethereum", address=None, metrics=None, evidence_list=None,
         flags=None, details=None) -> dict:
    out = {"id": id, "name": name, "kind": kind, "chain": chain,
           "metrics": dict(metrics or {}), "evidence": list(evidence_list or [])}
    if address:
        out["address"] = address.lower()
    if flags:
        out["flags"] = list(flags)
    if details:
        out["details"] = dict(details)
    return out


def edge(src, dst, mechanism, status, evidence_list, details=None) -> dict:
    out = {"from": src, "to": dst, "mechanism": mechanism, "status": status,
           "evidence": list(evidence_list)}
    if details:
        out["details"] = dict(details)
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _adjacency(graph):
    out = {n["id"]: [] for n in graph["nodes"]}
    for e in graph["edges"]:
        out.setdefault(e["from"], []).append(e["to"])
    return out


def find_cycle(graph):
    """Renvoie une liste de nœuds formant un cycle, ou None (DFS à trois couleurs)."""
    adj = _adjacency(graph)
    color = {n: 0 for n in adj}
    stack = []

    def visit(u):
        color[u] = 1
        stack.append(u)
        for v in adj.get(u, []):
            if color.get(v, 0) == 1:
                return stack[stack.index(v):] + [v]
            if color.get(v, 0) == 0:
                found = visit(v)
                if found:
                    return found
        stack.pop()
        color[u] = 2
        return None

    for n in list(adj):
        if color[n] == 0:
            found = visit(n)
            if found:
                return found
    return None


def depths(graph) -> dict:
    """Distance (nombre d'arêtes) de chaque nœud jusqu'à la position."""
    pos = graph["position"]["id"]
    rev = {}
    for e in graph["edges"]:
        rev.setdefault(e["to"], []).append(e["from"])
    dist = {pos: 0}
    queue = deque([pos])
    while queue:
        u = queue.popleft()
        for v in rev.get(u, []):
            if v not in dist:
                dist[v] = dist[u] + 1
                queue.append(v)
    return dist


def validate(graph) -> list:
    """Liste des erreurs (vide = graphe valide). Vérifications automatiques de cohérence."""
    errors = []
    ids = [n["id"] for n in graph.get("nodes", [])]
    id_set = set(ids)
    if len(ids) != len(id_set):
        errors.append("identifiants de nœuds en double")
    pos = graph.get("position", {}).get("id")
    if pos not in id_set:
        errors.append("la position n'est pas un nœud du graphe")
    for n in graph.get("nodes", []):
        if n.get("kind") not in KINDS:
            errors.append(f"nœud {n.get('id')} : type inconnu {n.get('kind')!r}")
    positions = [n["id"] for n in graph.get("nodes", []) if n.get("kind") == "position"]
    if positions != [pos]:
        errors.append("il doit y avoir exactement un nœud de type position")
    for e in graph.get("edges", []):
        label = f"arête {e.get('from')} → {e.get('to')}"
        if e.get("from") not in id_set or e.get("to") not in id_set:
            errors.append(f"{label} : extrémité inconnue")
        if e.get("mechanism") not in MECHANISMS:
            errors.append(f"{label} : mécanisme inconnu {e.get('mechanism')!r}")
        if e.get("status") not in STATUSES:
            errors.append(f"{label} : statut inconnu {e.get('status')!r}")
        ev = e.get("evidence") or []
        if not ev or not all(x.get("source") and x.get("url") and x.get("fetched_at") for x in ev):
            errors.append(f"{label} : preuve manquante ou incomplète")
        if e.get("from") == pos:
            errors.append(f"{label} : la position doit être le seul puits")
        src_kind = next((n["kind"] for n in graph["nodes"] if n["id"] == e.get("from")), None)
        if src_kind == "lending" and not (e.get("details") or {}).get("market_id"):
            errors.append(f"{label} : arête de prêt sans identifiant de marché réel")
    cycle = find_cycle(graph) if not errors else None
    if cycle:
        errors.append("cycle détecté : " + " → ".join(cycle))
    if pos in id_set:
        reach = depths(graph)
        for n in ids:
            if n not in reach:
                errors.append(f"nœud {n} : n'atteint pas la position")
    return errors


def coverage(graph) -> dict:
    """Résumé de couverture : nombre de dépendances, profondeur, part vérifiée par statut."""
    counts = {s: 0 for s in STATUS_ORDER}
    for e in graph["edges"]:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    total = max(len(graph["edges"]), 1)
    d = depths(graph)
    non_assumed = sum(v for k, v in counts.items() if k != "assumed")
    return {
        "dependencies": len(graph["nodes"]) - 1,
        "edges": len(graph["edges"]),
        "max_depth": max(d.values()) if d else 0,
        "by_status": counts,
        "share_by_status": {k: v / total for k, v in counts.items()},
        "verified_share": non_assumed / total,
    }
