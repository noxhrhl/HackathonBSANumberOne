"""
DeFi Risk Dependency Challenge - VERSION 1
Hardcoded data, but the whole pipeline runs end to end:
  1. build the dependency graph
  2. simulate an incident -> loss distribution
  3. compare to exit cost -> STAY or EXIT

Later you replace the hardcoded numbers with real data (DeFiLlama, Pendle...).
Run:  python risk_v1.py
"""
import networkx as nx
import numpy as np

# ---------------------------------------------------------------
# STEP 1 - THE GRAPH: what does our position depend on?
# An arrow A -> B means "if A breaks, B gets hit".
# w = how much of the damage passes through (1.0 = all of it, 0.5 = half)
# ---------------------------------------------------------------
POSITION = "PT position"

G = nx.DiGraph()
G.add_edge("Ethereum",           "Underlying (sUSDe)", w=1.0)
G.add_edge("Ethereum",           "Oracle",             w=1.0)
G.add_edge("Ethereum",           "Pendle AMM",         w=1.0)
G.add_edge("Ethereum",           "Lending market",     w=1.0)
G.add_edge("Underlying (sUSDe)", POSITION,             w=1.0)  # PT is backed by it
G.add_edge("Pendle AMM",         POSITION,             w=0.6)  # bad pool -> bad exit price
G.add_edge("Oracle",             "Lending market",     w=0.8)  # wrong price -> bad liquidations
G.add_edge("Lending market",     POSITION,             w=0.5)  # forced selling pushes PT down


def impact_factor(graph, node, target=POSITION):
    """Share of a failure at `node` that reaches the position (strongest path)."""
    if node == target:
        return 1.0
    best = 0.0
    for path in nx.all_simple_paths(graph, node, target):
        factor = 1.0
        for a, b in zip(path, path[1:]):
            factor *= graph[a][b]["w"]
        best = max(best, factor)
    return best


# ---------------------------------------------------------------
# STEP 2 - RISK OF EACH NODE (assumptions to justify with past incidents)
# p   = probability this node fails over our horizon
# sev = average loss of value of that node if it fails (0.5 = -50%)
# ---------------------------------------------------------------
RISK = {
    "Ethereum":           {"p": 0.001, "sev": 0.30},
    "Underlying (sUSDe)": {"p": 0.010, "sev": 0.50},
    "Oracle":             {"p": 0.010, "sev": 0.50},
    "Pendle AMM":         {"p": 0.010, "sev": 0.20},
    "Lending market":     {"p": 0.010, "sev": 0.30},
}

# The incident that just happened (layer 3 in Belem's example)
INCIDENT_NODE = "Oracle"
INCIDENT_P = 0.40          # given the incident, 40% chance it turns into real damage

# Portfolio numbers from the challenge sheet
POCKET_SHARE = 0.10        # this position = 10% of the book
EXIT_COST = 0.11           # selling costs 11% slippage
N_SIM = 100_000


def simulate(risk, incident_node, incident_p, n=N_SIM, seed=42):
    """Monte Carlo: returns n simulated losses on the position (0 to 1)."""
    rng = np.random.default_rng(seed)
    losses = np.zeros(n)
    for node, r in risk.items():
        p = incident_p if node == incident_node else r["p"]
        fails = rng.random(n) < p
        # severity varies around its mean (Beta distribution, stays in [0,1])
        a = 4.0
        b = a * (1 - r["sev"]) / r["sev"]
        sev = rng.beta(a, b, n)
        losses += fails * sev * impact_factor(G, node)
    return np.minimum(losses, 1.0)


# ---------------------------------------------------------------
# STEP 3 - DECISION
# ---------------------------------------------------------------
losses = simulate(RISK, INCIDENT_NODE, INCIDENT_P)
expected = losses.mean()
var95 = np.quantile(losses, 0.95)
cvar95 = losses[losses >= var95].mean()

# break-even: incident probability above which exiting is better
r = RISK[INCIDENT_NODE]
p_star = EXIT_COST / (r["sev"] * impact_factor(G, INCIDENT_NODE))

print("=== Dependencies of", POSITION, "===")
for node in RISK:
    print(f"  {node:20s} impact factor = {impact_factor(G, node):.2f}")

print(f"\n=== Incident on: {INCIDENT_NODE} (p = {INCIDENT_P:.0%}) ===")
print(f"Expected loss if STAY : {expected:6.1%} of pocket = {expected * POCKET_SHARE:5.2%} of book")
print(f"Worst 5% (CVaR95)     : {cvar95:6.1%} of pocket = {cvar95 * POCKET_SHARE:5.2%} of book")
print(f"Cost if EXIT          : {EXIT_COST:6.1%} of pocket = {EXIT_COST * POCKET_SHARE:5.2%} of book")

verdict = "EXIT" if expected > EXIT_COST else "STAY"
print(f"\nVERDICT: {verdict}")
print(f"Exit becomes optimal if the incident probability exceeds {p_star:.0%}.")
if verdict == "STAY" and cvar95 > EXIT_COST:
    print("Warning: on average staying is cheaper, but the worst case is far above the exit cost.")