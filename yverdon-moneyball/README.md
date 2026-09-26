# Moneyball YS — recrutement data pour Yverdon Sport

Système de recommandation de recrues (équipe masculine) qui croise deux sources actives :

1. **Transfermarkt**, en lecture directe des pages de www.transfermarkt.com (`moneyball/tm_direct.py`, bibliothèque standard uniquement) ou via [transfermarkt-api](https://github.com/felipeall/transfermarkt-api) si `TM_API_URL` est défini : valeur marchande, contrats, transferts, statistiques de championnat (matchs, buts, passes, cartons, minutes), buts encaissés et clean sheets des gardiens, classements (buts encaissés et rang des équipes), lieu de naissance, clubs formateurs.
2. **Le cockpit Fanbase Manager d'Yverdon Sport** (Brevo + Metricool) : indice de pénétration du bassin, catégories CMON_SCORE, performances des campagnes, audiences sociales.

Sources prévues, non actives sur le vivier livré : **API-Football** (api-sports.io, clé gratuite, 100 requêtes/jour : note moyenne, tirs, passes clés, duels ; module `apifootball.py`) et **Sofascore** (xG, xA ; sans API publique, répond 403 hors navigateur ; module `sofascore.py`). FBref n'est plus une option : plus de données avancées depuis la fin de son accord Opta (janvier 2026).

Chaque joueur du vivier reçoit trois notes sur 100 :

| Note | Ce qu'elle mesure |
|---|---|
| **Sport** | par sous-poste, en rang percentile dans le vivier : production (buts + 0,7 passes) / 90 pour milieux, attaquants et la moitié offensive des latéraux ; buts encaissés rapportés à l'équipe et clean sheets pour les gardiens ; résultats défensifs de l'équipe pour les centraux et la moitié défensive des latéraux ; plus disponibilité, discipline — corrigé du niveau de la ligue et des blessures ([détail](#note-sport-par-sous-poste)) |
| **Valeur** | sous-évaluation (régression log(valeur) ~ sport + âge + âge² + ligue + poste sur le vivier), levier fin de contrat, tendance de valeur 12 mois, plus-value nette à 24 mois ; valeur manquante estimée par la même régression, marquée et pesant moitié moins |
| **Fan fit** | ancrage régional (`geo.py`), francophonie, fidélité, potentiel média, lien avec le club — **pondérés par l'ADN fan tiré du cockpit** |

**Note finale** = moyenne pondérée (45 / 30 / 25 par défaut) × (0,85 + 0,30 × besoin du poste dans l'effectif YS). Pour un joueur à valeur marchande estimée, le poids Valeur est divisé par deux et les poids renormalisés.

### Comment le cockpit pilote le fan fit (`moneyball/fan_dna.py`)

| Signal cockpit | Valeur au 23.09.2026 | Effet |
|---|---|---|
| Indice de pénétration du bassin | 25,2 % (cœur 37,6 %) | poids **Ancrage** × 1,24 |
| Part de New Leads | 70,3 % (7,6 % Super Fans) | poids **Fidélité** × 1,34 |
| Ouverture annonce des recrues vs moyenne | 55,0 % vs 51,8 % | poids **Média** × 1,06 |

Poids résultants : ancrage 32 %, fidélité 23 %, média 18 %, francophonie 13 %, lien club 13 %. Ils se recalculent à chaque mise à jour de `data/fanbase_cockpit.json`.

### Note Sport par sous-poste

Source : Transfermarkt seul, deux dernières saisons de championnat. Chaque joueur est lu comme rang percentile dans son sous-poste (joueurs du vivier à 900 minutes et plus), après rétrécissement vers la médiane du sous-poste quand l'échantillon est court : confiance = minutes / (minutes + 900), et valeur retenue = confiance × observé + (1 − confiance) × médiane.

| Sous-poste | Qualité (percentile) | Poids qualité / disponibilité / discipline | Confiance |
|---|---|---|---|
| Milieux (MID) | (buts + 0,7 passes) / 90 × niveau | 45 / 45 / 10 | minutes |
| Attaquants (ATT) | idem | 60 / 30 / 10 | minutes |
| Gardiens (GK) — `sport_score_gk` | indice défensif = taux de clean sheets + terme « buts encaissés » : gardien partagé (< 85 % des minutes de l'équipe) → 1 − (ses buts encaissés/90 ÷ ceux de l'équipe), borné ± 0,5 ; titulaire unique (rapport = 1 par construction) → rang de l'équipe au classement, −0,3 (dernier) à +0,3 (premier) ; percentile **entre gardiens seulement** | 65 / 25 (plafond) / 10 | minutes ; **faible** (≤ 0,5) sans donnée de buts encaissés ou sans classement |
| Centraux (CB) — `sport_score_def` | résultats défensifs de l'équipe : 0,5 + (médiane ligue − buts encaissés/match de l'équipe) / médiane / 2, borné 0-1 ; percentile entre centraux | 55 / 30 / 15 (cartons en négatif) | **faible** (plafonnée à 0,5) : aucune donnée individuelle de défense |
| Latéraux (FB) — `sport_score_def` | 50 % profil défensif (formule des centraux) + 50 % production offensive (percentile entre latéraux) | 55 / 35 / 10 | minutes |

Puis × (0,55 + 0,45 × niveau), niveau = coefficient de ligue / 1,45 borné entre 0,3 et 1,2, × (1 − jours de blessure / 180, au plus 0,5), / 0,85, borné à 100. Jamais de buts ni de passes dans la note d'un gardien ; jamais de buts dans celle d'un central.

Le sous-poste vient du champ `position` Transfermarkt : `Left-Back`, `Right-Back` (et « wing-back ») → latéral ; tout autre défenseur → central.

### Coefficients de ligue

Le **niveau** d'un joueur est la moyenne des coefficients de ses championnats pondérée par ses minutes (`recent_stats`), sur ses deux dernières saisons ayant des minutes, en ne gardant que les compétitions à coefficient connu (coupes et lignes « Total » ignorées ; si aucune n'est connue, toutes les lignes comptent au coefficient 0,80). Référence : Challenge League = 1,00.

| Code Transfermarkt | Championnat | Coefficient |
|---|---|---|
| C1 / C2 / CHPL | Super League / Challenge League / Promotion League | 1,45 / 1,00 / 0,70 |
| FR1 / FR2 / FR3 | Ligue 1 / Ligue 2 / National | 2,10 / 1,45 / 1,00 |
| BE1 / BE2 | Pro League / Challenger Pro League | 1,70 / 1,05 |
| L2 / L3 | 2. Bundesliga / 3. Liga | 1,75 / 1,20 |
| A1 / A2 | Bundesliga autrichienne / 2. Liga | 1,35 / 0,95 |
| IT1 / IT2 / IT3 | Serie A / Serie B / Serie C | 2,10 / 1,60 / 0,90 |
| NL1 / NL2 | Eredivisie / Eerste Divisie | 1,80 / 1,10 |
| PO1 / PO2 | Liga Portugal / Liga 2 | 1,50 / 0,90 |
| ES1 / ES2 | LaLiga / LaLiga 2 | 2,10 / 1,60 |
| GB1 / GB2 | Premier League / Championship | 2,50 / 1,70 |
| PL1, GR1, MLS1 | Ekstraklasa, Super League grecque, MLS | 1,40 |
| TR1 | Süper Lig | 1,60 |
| DK1 / DK2 | Superliga / 1. Division danoises | 1,30 / 0,80 |
| SE1, NO1 | Allsvenskan, Eliteserien | 1,20 |
| RO1, KR1 | Liga 1 roumaine, K League 1 | 1,10 |
| SER1 | Super Liga serbe | 1,00 |
| BU1 | Bulgarie | 0,90 |
| LUX1 | Luxembourg | 0,60 |

Divisions inférieures et groupes régionaux, par **préfixe** du code (FR4A, FR4B… partagent le coefficient FR4) :

| Préfixe | Championnat | Coefficient |
|---|---|---|
| FR4 / FR5 | National 2 / National 3 | 0,65 / 0,45 |
| CHC / CIR | 1re Ligue Classic / 2e Ligue interrégionale | 0,45 / 0,30 |
| C19 / F19 | U19 Elite suisse / National U19 | 0,35 / 0,35 |
| GB21 | Premier League 2 | 0,70 |
| IT3 | Serie C (groupes A-C) | 0,90 |
| E3G / E4G | Primera / Segunda Federación | 0,90 / 0,60 |
| RL | Regionalliga | 0,70 |
| BE3 | 1ste Nationale | 0,60 |
| PT23 | Liga Next Gen U23 | 0,60 |

Correction du 26.09.2026 : `IT3` valait 1,00 en code exact et 0,90 en préfixe (`IT3A`…) pour la même Serie C ; les deux valent désormais 0,90. Les valeurs 0,45 / 0,65 / 0,92 / 1,07 / 1,31 observées dans les données sont ces moyennes pondérées, par exemple :

> Laurent Seji (gardien, Stade-Lausanne-Ouchy) : 720 + 360 = 1 080 minutes en Challenge League (1,00) et 900 minutes en 1re Ligue Classic (`CHC2`, 0,45) ; la ligne Super League 25/26 à 0 minute est ignorée. Niveau = (1 080 × 1,00 + 900 × 0,45) / 1 980 = **0,75**.

### Géographie du fan fit (`moneyball/geo.py`)

Les lieux de naissance Transfermarkt sont écrits dans la langue de la fiche (Genf / Genève / Geneva, Freiburg / Fribourg, Biel/Bienne / Biel-Bienne / Bienne, Sitten / Sion, Neuenburg / Neuchâtel, Delsberg / Delémont, Pruntrut / Porrentruy, Murten / Morat…). `geo.normalize` (minuscules, accents, tirets, barres, « près », « sur », « les », suffixes de canton) puis une table commune → canton rattachent chaque lieu à une zone : bassin nord-vaudois (Jura-Nord vaudois + Broye-Vully, ancrage 1,00), Vaud (0,85), Romandie (GE, FR, NE, JU, VS hors communes germanophones, Jura bernois et Bienne ; 0,70), Suisse (0,45), frontalier (Ain, Doubs, Jura, Haute-Savoie, Territoire de Belfort, Sud-Alsace ; 0,40), international (0,10).

Règle du club formateur, identique pour tous : un passage dans la formation d'Yverdon Sport vaut « bassin », un club formateur romand (`geo.ROMAND_CLUBS`, noms entiers) vaut « Romandie » ; on retient la meilleure des deux zones (naissance, formation).

Lieux de naissance du vivier restés non reconnus (joueurs nés en Suisse ou sans pays renseigné) : **aucun** au 26.09.2026 (les 166 joueurs nés en Suisse sont tous rattachés à une commune ; 224 joueurs n'ont pas de lieu de naissance sur Transfermarkt et sont notés d'après leur nationalité et leur club formateur). `python -m moneyball.recommend` recalcule la liste dans `data/recommendations.json` (`lieux_non_reconnus`, 20 plus fréquents) pour l'ajouter à la main dans `geo.py` si de nouveaux lieux apparaissent.

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

# 2b. Rafraîchir seulement les statistiques (clubs, clean sheets, classements ; ≈ 20 min), sans relire les fiches
python -m moneyball.scout --stats-only
python -m moneyball.defence            # ou seulement la partie défensive (clean sheets + classements)

# Variantes : instance transfermarkt-api pour profils/valeurs/transferts, enrichissement API-Football
export TM_API_URL=http://localhost:8000   # docker run -p 8000:8000 transfermarkt-api
export API_FOOTBALL_KEY=...               # clé gratuite : https://dashboard.api-football.com
python -m moneyball.scout --competitions C2 C1 FR3 FR2 --apifootball 208 207
```

Le vivier livré (`data/candidates.json`, 26.09.2026) est réel : Challenge League, Super League, Ligue 3 et Ligue 2 françaises, joueurs de 17 à 29 ans valant au plus 1,5 M€, deux saisons de championnat par joueur.

`--apifootball` lit les statistiques saison de chaque championnat (20 joueurs par page ; Challenge + Super League ≈ 35 requêtes, sous les 100 du plan gratuit) et rattache chaque candidat Transfermarkt par **date de naissance exacte + nom**. `python -m moneyball.apifootball --find "Challenge League"` donne l'id d'un championnat ; `python -m moneyball.apifootball --merge` enrichit après coup.

`--sofascore` (optionnel) récupère les statistiques saison de chaque tournoi Sofascore (216 = Challenge League, 215 = Super League ; l'id d'un autre championnat se lit en fin d'URL sur sofascore.com) et les rattache aux candidats par nom normalisé + année de naissance. `python -m moneyball.sofascore --merge` fait la même chose après coup. Sofascore n'a pas d'API publique : usage interne, un appel par seconde, cache 72 h.

Tag Moneyball : un joueur à ≥ 900 minutes qui marque nettement moins que ses xG est étiqueté **Sous-performe ses xG** (occasion d'achat) ; l'inverse **Sur-performe ses xG** (saison de chance).

`scout.py` trouve Yverdon Sport par recherche (ou `--club-id`), lit l'effectif pour calculer les besoins, parcourt les championnats demandés, filtre sur âge et valeur, puis récupère profil, historique de valeur et transferts de chaque joueur. Les statistiques viennent de la page « Squad statistics » de chaque club (une page par club, championnat et saison : saison en cours + précédente, plus l'ancien club des joueurs arrivés depuis un an), car Transfermarkt rend désormais les statistiques individuelles côté client — `/players/{id}/stats` de transfermarkt-api renvoie une liste vide. Cette page ne contient pas les colonnes buts encaissés / clean sheets (vérifié le 26.09.2026 ; le parseur les lirait si elles apparaissaient) : `defence.py` les lit sur la page « Clean sheets » de chaque compétition (vue détaillée `weisseWeste/…/plus/1`, une ligne par gardien : matchs, clean sheets, buts encaissés, minutes) et lit le classement de chaque compétition (buts encaissés, matchs et rang de chaque équipe). Chaque ligne `stats[]` d'un joueur porte alors `club_id`, `conceded` et `clean_sheets` (gardiens, None sinon), `team_conceded`, `team_matches`, `team_rank`, `team_count` et `league_conceded_90_median` ; `data/defence.json` garde les pages lues. Les pages sont mises en cache 72 h dans `data/cache/` ; Transfermarkt sert un captcha (HTTP 405) sur environ une requête sur deux, réessayé automatiquement. `--workers` règle le parallélisme (3 par défaut, cadence globale ≈ 1 requête/s).

Codes compétition Transfermarkt usuels : `C1` Super League, `C2` Challenge League, `FR2` Ligue 2, `FR3` National, `BE2` Challenger Pro League, `A2` 2. Liga autrichienne, `L3` 3. Liga. Vérifier un code avec `/competitions/search/{nom}`.

## Fichiers

```
moneyball/tm_direct.py   lecture directe de www.transfermarkt.com (parseur HTML, cache, captcha)
moneyball/tm_client.py   client transfermarkt-api (option, TM_API_URL)
moneyball/scout.py       constitution du vivier + normalisation (--stats-only : rafraîchit les statistiques)
moneyball/defence.py     clean sheets / buts encaissés des gardiens, classements → colonnes défensives
moneyball/geo.py         toponymes multilingues → commune → canton → zone d'ancrage
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
- Sources prévues, non actives : Sofascore répond `403 Forbidden` à toute requête hors navigateur (vérifié le 26.09.2026 avec plusieurs User-Agent et les en-têtes Origin/Referer) ; API-Football n'a pas tourné (pas de clé). Le vivier livré n'a ni xG, ni xA, ni note : la note Sport repose sur buts, passes, minutes, discipline, buts encaissés des gardiens et résultats d'équipe Transfermarkt.
- Transfermarkt ne donne aucune statistique défensive individuelle : les centraux sont notés sur les résultats de leur équipe (confiance « faible ») ; un gardien titulaire unique est noté sur le classement de son équipe, pas sur ses arrêts.
- Buts encaissés : 96 gardiens sur 123 ont une donnée (page « Clean sheets » des 30 couples compétition/saison où joue un gardien du vivier) ; les 27 autres n'ont aucune minute en championnat ou ne jouent que dans une compétition sans page clean sheets (ligne « Total », OFPL, TS2…) et gardent une confiance « faible ». Les résultats d'équipe (classement) couvrent 1093 joueurs sur 1171.
- Valeurs marchandes manquantes : 129 joueurs sans valeur Transfermarkt reçoivent la valeur prédite par la régression (`market_value_estimated`, `market_value_source: "regression"`) ; ils sont exclus du plan mercato automatique et comptés à part.
- Statistiques Transfermarkt limitées au championnat des deux dernières saisons ; un joueur arrivé d'un championnat hors périmètre n'a que sa saison précédente dans son ancien club ; les blessures ne sont lues qu'avec transfermarkt-api (`--injuries`).
- Début de saison 2026/27 : les minutes de la saison en cours sont encore faibles, le modèle s'appuie surtout sur 2025/26.
- L'âge des fans n'est pas modélisé (date de naissance renseignée à 36 % dans Brevo).
- Périmètre : équipe masculine uniquement.
- L'indemnité estimée est un ordre de grandeur, pas une offre.
