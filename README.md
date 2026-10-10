# Belem Capital — prototype de risque DeFi

Cette première version montre **comment** une position peut être reliée à ses dépendances et comment comparer la perte d'un scénario au coût d'une sortie. Les données de `data/demo.json` sont **illustratives** : aucun prix réel ni probabilité d'incident n'est encore récupéré.

## Démarrer

Il faut Python 3.10 ou plus récent. Aucune installation de paquet n'est nécessaire.

```bash
python3 app.py
```

Ouvrir ensuite <http://127.0.0.1:8000>. Pour choisir un autre port : `python3 app.py --port 8001`. Arrêter le serveur avec `Ctrl+C`.

## Ce que montre la page

1. La position d'exemple et cinq étages de dépendance. Les étages « à vérifier » ou « non utilisé » sont distingués des dépendances retenues dans le scénario.
2. Trois champs modifiables : valeur de la position, perte **si l'incident se produit**, et coût de sortie immédiate.
3. Deux montants et un seuil de probabilité. Par exemple, si sortir coûte 300 USD et qu'un incident causerait 2 000 USD de perte, le seuil est `300 / 2 000 = 15 %`. **Ce seuil n'est pas une estimation de la probabilité réelle de l'incident.**

Ce calcul volontairement simple compare une seule période et un seul scénario. Il n'inclut pas encore la variation du prix du PT, la liquidité pour une taille d'ordre donnée, les frais de transaction, les incidents combinés ou un emprunt garanti par le PT.

## Fichiers

| Fichier | Rôle |
| --- | --- |
| `app.py` | Serveur local, lecture du formulaire et affichage de la page |
| `risk.py` | Calcul financier isolé de l'interface |
| `data/demo.json` | Position, scénario et dépendances illustratives |
| `templates/index.html` | Structure de la page |
| `static/style.css` | Mise en forme et schéma visuel |
| `tests/test_risk.py` | Vérification des cas essentiels du calcul |

Pour lancer les vérifications :

```bash
python3 -m unittest discover -s tests
```

## Comment continuer

**Données.** Choisir un marché PT réel et remplacer les valeurs illustratives de `data/demo.json`. Commencer par l'[API officielle Pendle](https://docs.pendle.finance/pendle-v2-dev/Quickstart), puis vérifier les adresses et les liens dans la documentation des protocoles. Ajouter à chaque valeur sa source et l'heure du relevé. Ne pas présenter un lien supposé comme vérifié.

**Dépendances.** Aujourd'hui, la page affiche des fiches, pas un graphe découvert automatiquement. Relier chaque composant par des identifiants de contrats et décrire le mécanisme exact de propagation d'un incident. Un marché de prêt ne doit devenir une dépendance de la position que si le PT y est réellement déposé.

**Risque.** Remplacer les pourcentages saisis par des scénarios justifiés. Estimer le coût de sortie à partir d'une cotation ou de la profondeur du marché pour **la taille de la position**. Ajouter d'autres scénarios seulement après avoir validé le premier avec une personne qui connaît le marché.

**Interface.** Le calcul est dans `risk.py` et les données dans `data/demo.json` : on peut enrichir ou remplacer l'interface sans changer ces deux parties.

Le brief original est conservé dans `BelemCapital_EPFL-Hackathon_DeFi-Risk-Dependency-Challenge_EN_v1.pdf`.
