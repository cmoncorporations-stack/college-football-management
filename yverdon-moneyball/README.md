# Moneyball YS — recrutement data pour Yverdon Sport

Système de recommandation de recrues qui croise deux sources :

1. **Transfermarkt**, via [transfermarkt-api](https://github.com/felipeall/transfermarkt-api) : performance, valeur marchande, contrats, transferts, blessures.
2. **Le cockpit Fanbase Manager d'Yverdon Sport** (Brevo + Metricool) : indice de pénétration du bassin, catégories CMON_SCORE, performances des campagnes, audiences sociales.

Chaque joueur du vivier reçoit trois notes sur 100 :

| Note | Ce qu'elle mesure |
|---|---|
| **Sport** | (buts + 0,7 passes) / 90 min vs attendu du poste, disponibilité, discipline — corrigé du niveau de la ligue et des blessures |
| **Valeur** | sous-évaluation (régression log(valeur) ~ sport + âge sur le vivier), levier fin de contrat, tendance de valeur 12 mois, potentiel de revente |
| **Fan fit** | ancrage régional, francophonie, fidélité, potentiel média, lien avec le club — **pondérés par l'ADN fan tiré du cockpit** |

**Note finale** = moyenne pondérée (45 / 30 / 25 par défaut) × (0,85 + 0,30 × besoin du poste dans l'effectif YS).

### Comment le cockpit pilote le fan fit (`moneyball/fan_dna.py`)

| Signal cockpit | Valeur au 23.09.2026 | Effet |
|---|---|---|
| Indice de pénétration du bassin | 25,2 % (cœur 37,6 %) | poids **Ancrage** × 1,24 |
| Part de New Leads | 70,3 % (7,6 % Super Fans) | poids **Fidélité** × 1,34 |
| Ouverture annonce des recrues vs moyenne | 55,0 % vs 51,8 % | poids **Média** × 1,06 |

Poids résultants : ancrage 32 %, fidélité 23 %, média 18 %, francophonie 13 %, lien club 13 %. Ils se recalculent à chaque mise à jour de `data/fanbase_cockpit.json`.

## Utilisation

Python 3.10+, aucune dépendance.

```bash
cd yverdon-moneyball

# 1. Démo immédiate (vivier FICTIF, signaux fans réels)
python -m moneyball.recommend --demo
open dashboard.html

# 2. Données réelles Transfermarkt
export TM_API_URL=http://localhost:8000   # conseillé : instance locale (docker run -p 8000:8000 transfermarkt-api)
python -m moneyball.scout --competitions C2 C1 FR3 FR2 BE2 --max-value 1500000 --max-age 29
python -m moneyball.recommend
```

`scout.py` trouve Yverdon Sport par recherche (ou `--club-id`), lit l'effectif pour calculer les besoins, parcourt les championnats demandés, filtre sur âge et valeur, puis récupère profil, stats, historique de valeur et transferts de chaque joueur (`--injuries` pour les blessures). Les réponses sont mises en cache 72 h dans `data/cache/`. L'instance publique `transfermarkt-api.fly.dev` est limitée en débit : pour un scan de plusieurs championnats, héberger sa propre instance.

Codes compétition Transfermarkt usuels : `C1` Super League, `C2` Challenge League, `FR2` Ligue 2, `FR3` National, `BE2` Challenger Pro League, `A2` 2. Liga autrichienne, `L3` 3. Liga. Vérifier un code avec `/competitions/search/{nom}`.

## Fichiers

```
moneyball/tm_client.py   client transfermarkt-api (cache, retries, rate limit)
moneyball/scout.py       constitution du vivier + normalisation
moneyball/fan_dna.py     ADN fan dérivé du cockpit → poids du fan fit
moneyball/model.py       notes Sport / Valeur / Fan fit, besoins de l'effectif
moneyball/recommend.py   classement → data/recommendations.json + dashboard.html
moneyball/demo.py        vivier fictif de démonstration
data/fanbase_cockpit.json  extrait du cockpit Fanbase Manager (relevé du 23.09.2026)
dashboard_template.html  tableau de bord interactif (poids, filtres, plan mercato)
tests/                   python -m unittest discover -s tests -t .
```

## Limites

- Transfermarkt n'a ni tracking ni audience des comptes joueurs : le potentiel média repose sur la présence d'un compte Instagram et sur la contribution offensive.
- L'âge des fans n'est pas modélisé (date de naissance renseignée à 36 % dans Brevo).
- L'équipe féminine (Instagram +22 % en 180 jours) n'est pas couverte : Transfermarkt suit mal le football féminin.
- L'indemnité estimée est un ordre de grandeur, pas une offre.
