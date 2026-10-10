"""Outil de collecte du cache (utilisé pendant le hackathon quand le shell n'a pas Internet).

  python tools/cache_tool.py missing          → liste JSON des requêtes absentes du cache
  python tools/cache_tool.py write blob.json  → écrit les réponses relevées ailleurs (navigateur)

Les réponses sont les mêmes que celles que `sources.py` enregistre en ligne
(mêmes URL, mêmes requêtes, mêmes réductions). En temps normal, il suffit de lancer
`python pipeline.py --all` avec Internet : le cache se remplit tout seul.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from risk_engine import sources as S  # noqa: E402


def missing():
    from risk_engine import discover
    S.set_offline(True)
    todo = S.start_collect()
    errors = []
    for p in discover.POSITIONS["positions"]:
        try:
            discover.discover(p["id"])
        except S.MissingCache as exc:
            errors.append(f"{p['id']}: {exc}")
        except Exception as exc:  # une réponse manquante peut casser un résolveur : on note et on continue
            errors.append(f"{p['id']}: {type(exc).__name__} {exc}")
    try:
        import pipeline
        for p in discover.POSITIONS["positions"]:
            try:
                pipeline.exit_quotes_for(p["id"])
            except Exception as exc:
                errors.append(f"quotes {p['id']}: {type(exc).__name__} {exc}")
    except ImportError:
        pass
    seen, out = set(), []
    for r in todo:
        k = (r["source"], r["key"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    print(json.dumps({"missing": out, "errors": errors}, indent=1, ensure_ascii=False))


def write(blob_path):
    blob = json.loads(Path(blob_path).read_text(encoding="utf-8"))
    n = 0
    for e in blob["entries"]:
        S.write_cache_entry(e["source"], e["key"], e["url"], e.get("request"), e["response"], e["fetched_at"])
        n += 1
    print(f"{n} entrées écrites")


if __name__ == "__main__":
    if sys.argv[1] == "missing":
        missing()
    else:
        write(sys.argv[2])
