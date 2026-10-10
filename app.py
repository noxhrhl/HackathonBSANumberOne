"""Petite interface locale pour explorer une position et un scénario illustratifs."""

import argparse
import json
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from string import Template
from urllib.parse import parse_qs, urlparse

from risk import assess


ROOT = Path(__file__).resolve().parent
TEMPLATE = Template((ROOT / "templates" / "index.html").read_text(encoding="utf-8"))
STYLE = (ROOT / "static" / "style.css").read_bytes()


def display_number(value: float, decimals: int = 0) -> str:
    """Espace les milliers selon une présentation lisible en français."""
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")


def render_page(query: str) -> str:
    demo = json.loads((ROOT / "data" / "demo.json").read_text(encoding="utf-8"))
    fields = parse_qs(query, keep_blank_values=True)
    defaults = demo["defaults"]
    raw = {
        name: fields.get(name, [str(defaults[name])])[0]
        for name in ("position_usd", "loss_percent", "exit_percent")
    }

    error = ""
    result_html = ""
    try:
        result = assess(*(float(raw[name]) for name in raw))
    except (ValueError, OverflowError) as exc:
        error = f'<p class="error" role="alert">{escape(str(exc))}</p>'
    else:
        loss = result["scenario_loss_usd"]
        exit_cost = result["exit_cost_usd"]
        threshold = result["break_even_probability_percent"]
        largest = max(loss, exit_cost, 1)
        loss_width = 100 * loss / largest
        exit_width = 100 * exit_cost / largest
        if threshold is None:
            decision = "Ce scénario ne produit aucune perte : aucun seuil de probabilité à calculer."
            threshold_label = "—"
        elif threshold > 100:
            decision = "Même si cet incident était certain, la perte de ce scénario resterait inférieure au coût de sortie."
            threshold_label = f"{display_number(threshold, 1)} %"
        else:
            decision = (
                f"Si vous estimez la probabilité de cet incident au-dessus de "
                f"{display_number(threshold, 1)} %, sortir coûte moins cher dans ce modèle simplifié."
            )
            threshold_label = f"{display_number(threshold, 1)} %"

        result_html = f"""
        <div class="metrics">
          <div class="metric"><span>Perte si l'incident survient</span><strong>{display_number(loss)} USD</strong></div>
          <div class="metric"><span>Coût de sortie immédiate</span><strong>{display_number(exit_cost)} USD</strong></div>
          <div class="metric accent"><span>Seuil de probabilité</span><strong>{threshold_label}</strong></div>
        </div>
        <div class="comparison" aria-label="Comparaison visuelle des montants">
          <div class="bar-row"><span>Rester, si incident</span><div class="bar-track"><div class="bar loss" style="width: {loss_width:.1f}%"></div></div></div>
          <div class="bar-row"><span>Sortir maintenant</span><div class="bar-track"><div class="bar exit" style="width: {exit_width:.1f}%"></div></div></div>
        </div>
        <p class="decision">{escape(decision)}</p>
        <p class="fine-print">Seuil = coût de sortie ÷ perte dans le scénario. Ce seuil n'est pas la probabilité réelle de l'incident.</p>
        """

    cards = []
    for dependency in demo["dependencies"]:
        hit = dependency["id"] == demo["scenario_target"]
        cards.append(
            f'<article class="dependency {"is-hit" if hit else ""}">'
            f'<div class="layer">ÉTAGE {int(dependency["layer"]):02d}</div>'
            f'<div class="dependency-text"><h3>{escape(dependency["name"])}</h3>'
            f'<p>{escape(dependency["description"])}</p></div>'
            f'<span class="status">{escape(dependency["status"])}</span></article>'
        )

    substitutions = {
        "position_name": escape(demo["position_name"]),
        "scenario_name": escape(demo["scenario_name"]),
        "demo_note": escape(demo["demo_note"]),
        "dependencies": "\n".join(cards),
        "error": error,
        "result": result_html,
        **{name: escape(value, quote=True) for name, value in raw.items()},
    }
    return TEMPLATE.substitute(substitutions)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        request = urlparse(self.path)
        if request.path == "/static/style.css":
            body, content_type, status = STYLE, "text/css; charset=utf-8", 200
        elif request.path == "/health":
            body, content_type, status = b"ok", "text/plain; charset=utf-8", 200
        elif request.path == "/":
            body = render_page(request.query).encode("utf-8")
            content_type, status = "text/html; charset=utf-8", 200
        else:
            body, content_type, status = b"Page introuvable", "text/plain; charset=utf-8", 404
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prototype local de risque DeFi")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Ouvrez http://127.0.0.1:{args.port} (Ctrl+C pour arrêter)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServeur arrêté.")
    finally:
        server.server_close()
