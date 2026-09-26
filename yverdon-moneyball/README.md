# Moneyball YS — recrutement data pour Yverdon Sport

Système de recommandation de recrues (équipe masculine) qui croise trois sources :

1. **Transfermarkt**, en lecture directe des pages de www.transfermarkt.com (`moneyball/tm_direct.py`, bibliothèque standard uniquement) ou via [transfermarkt-api](https://github.com/felipeall/transfermarkt-api) si `TM_API_URL` est défini : valeur marchande, contrats, transferts, statistiques de championnat (matchs, buts, passes, cartons, minutes), lieu de naissance, clubs formateurs.
2. **API-Football** (api-sports.io) : l'API gratuite documentée retenue. Clé gratuite, 100 requêtes/jour, tous les points d'entrée, 1 100+ championnats dont Challenge League (208) et Super League (207). Par joueur et par saison : note moyenne, tirs, passes clés, duels, dribbles, cartons, date de naissance. Pas de xG au niveau saison.
   En option, **Sofascore** (sans API publique) ajoute xG et xA. FBref n'est plus une option : plus de données avancées depuis la fin de son accord Opta (janvier 2026).
3. **Le cockpit Fanbase Manager d'Yverdon Sport** (Brevo + Metricool) : indice de pénétration du bassin, catégories CMON_SCORE, performances des campagnes, audiences sociales.

Chaque joueur du vivier reçoit trois notes sur 100 :

| Note | Ce qu'elle mesure |
|---|---|
| **Sport** | production / 90 min vs attendu du poste : (xG + 0,7 xA) Sofascore, sinon (buts + 0,7 passes + 0,1 passe clé) API-Football, sinon (buts + 0,7 passes) Transfermarkt ; note moyenne pour un tiers ; disponibilité, discipline — corrigé du niveau de la ligue et des blessures |
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

# 2. Données réelles Transfermarkt (lecture directe, rien à installer ; ≈ 1 h pour 4 championnats)
python -m moneyball.scout --competitions C2 C1 FR3 FR2 --max-value 1500000 --max-age 29
python -m moneyball.recommend

# Variantes : instance transfermarkt-api pour profils/valeurs/transferts, enrichissement API-Football
export TM_API_URL=http://localhost:8000   # docker run -p 8000:8000 transfermarkt-api
export API_FOOTBALL_KEY=...               # clé gratuite : https://dashboard.api-football.com
python -m moneyball.scout --competitions C2 C1 FR3 FR2 --apifootball 208 207
```

Le vivier livré (`data/candidates.json`, 26.09.2026) est réel : Challenge League, Super League, Ligue 3 et Ligue 2 françaises, joueurs de 17 à 29 ans valant au plus 1,5 M€, deux saisons de championnat par joueur.

`--apifootball` lit les statistiques saison de chaque championnat (20 joueurs par page ; Challenge + Super League ≈ 35 requêtes, sous les 100 du plan gratuit) et rattache chaque candidat Transfermarkt par **date de naissance exacte + nom**. `python -m moneyball.apifootball --find "Challenge League"` donne l'id d'un championnat ; `python -m moneyball.apifootball --merge` enrichit après coup.

`--sofascore` (optionnel) récupère les statistiques saison de chaque tournoi Sofascore (216 = Challenge League, 215 = Super League ; l'id d'un autre championnat se lit en fin d'URL sur sofascore.com) et les rattache aux candidats par nom normalisé + année de naissance. `python -m moneyball.sofascore --merge` fait la même chose après coup. Sofascore n'a pas d'API publique : usage interne, un appel par seconde, cache 72 h.

Tag Moneyball : un joueur à ≥ 900 minutes qui marque nettement moins que ses xG est étiqueté **Sous-performe ses xG** (occasion d'achat) ; l'inverse **Sur-performe ses xG** (saison de chance).

`scout.py` trouve Yverdon Sport par recherche (ou `--club-id`), lit l'effectif pour calculer les besoins, parcourt les championnats demandés, filtre sur âge et valeur, puis récupère profil, historique de valeur et transferts de chaque joueur. Les statistiques viennent de la page « Squad statistics » de chaque club (une page par club, championnat et saison : saison en cours + précédente, plus l'ancien club des joueurs arrivés depuis un an), car Transfermarkt rend désormais les statistiques individuelles côté client — `/players/{id}/stats` de transfermarkt-api renvoie une liste vide. Les pages sont mises en cache 72 h dans `data/cache/` ; Transfermarkt sert un captcha (HTTP 405) sur environ une requête sur deux, réessayé automatiquement. `--workers` règle le parallélisme (3 par défaut, cadence globale ≈ 1 requête/s).

Codes compétition Transfermarkt usuels : `C1` Super League, `C2` Challenge League, `FR2` Ligue 2, `FR3` National, `BE2` Challenger Pro League, `A2` 2. Liga autrichienne, `L3` 3. Liga. Vérifier un code avec `/competitions/search/{nom}`.

## Fichiers

```
moneyball/tm_direct.py   lecture directe de www.transfermarkt.com (parseur HTML, cache, captcha)
moneyball/tm_client.py   client transfermarkt-api (option, TM_API_URL)
moneyball/scout.py       constitution du vivier + normalisation
moneyball/apifootball.py API-Football : notes, tirs, passes clés… (API gratuite retenue)
moneyball/sofascore.py   Sofascore : xG / xA / notes (optionnel, sans API publique)
moneyball/fan_dna.py     ADN fan dérivé du cockpit → poids du fan fit
moneyball/model.py       notes Sport / Valeur / Fan fit, besoins de l'effectif
moneyball/recommend.py   classement → data/recommendations.json + dashboard.html
moneyball/demo.py        vivier fictif de démonstration
data/fanbase_cockpit.json  extrait du cockpit Fanbase Manager (relevé du 23.09.2026)
dashboard_template.html  tableau de bord interactif (poids, filtres, plan mercato)
tests/                   python -m unittest discover -s tests -t .
```

## Limites

- Ni Transfermarkt ni Sofascore ne donnent l'audience des comptes joueurs : le potentiel média repose sur la présence d'un compte Instagram et sur la contribution offensive.
- Sofascore répond `403 Forbidden` à toute requête hors navigateur (vérifié le 26.09.2026 avec plusieurs User-Agent et les en-têtes Origin/Referer) : le vivier livré n'a ni xG, ni xA, ni note ; la note Sport repose sur buts, passes, minutes et discipline Transfermarkt. Le module API-Football n'a pas tourné (pas de clé).
- Statistiques Transfermarkt limitées au championnat des deux dernières saisons ; un joueur arrivé d'un championnat hors périmètre n'a que sa saison précédente dans son ancien club ; les blessures ne sont lues qu'avec transfermarkt-api (`--injuries`).
- Début de saison 2026/27 : les minutes de la saison en cours sont encore faibles, le modèle s'appuie surtout sur 2025/26.
- L'âge des fans n'est pas modélisé (date de naissance renseignée à 36 % dans Brevo).
- Périmètre : équipe masculine uniquement.
- L'indemnité estimée est un ordre de grandeur, pas une offre.
