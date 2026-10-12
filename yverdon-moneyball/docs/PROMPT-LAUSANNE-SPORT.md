# Prompt — recréer le système Moneyball pour le FC Lausanne-Sport

À coller tel quel dans une nouvelle session Claude Code, environnement cloud **« C'mon Sports »** (celui dont l'accès réseau autorise `www.transfermarkt.com`, `api.sofascore.com`, `transfermarkt-api.fly.dev`), dépôt `cmoncorporations-stack/college-football-management`, branche de départ `claude/yverdon-moneyball-system-uwrjyd`.

---

## Contexte

Le dossier `yverdon-moneyball/` du dépôt contient un système Moneyball complet et fonctionnel pour Yverdon Sport (équipe masculine). Lis d'abord `yverdon-moneyball/README.md`, puis `moneyball/model.py`, `moneyball/fan_dna.py`, `moneyball/scout.py`, `moneyball/recommend.py` et `dashboard_template.html`. Ne réécris pas ce qui existe : **le travail consiste à paramétrer ce système pour le FC Lausanne-Sport (LS)** et à produire son propre vivier, son propre tableau de bord et sa propre branche.

Le système croise trois sources :
1. **Transfermarkt**, lu directement (`moneyball/tm_direct.py`, bibliothèque standard, cache disque, gestion du captcha AWS) : effectif, valeur marchande et historique, contrats, transferts, lieu de naissance, clubs formateurs, statistiques de championnat lues sur les pages « Squad statistics » de chaque club (saison en cours + précédente).
2. **Le cockpit Fanbase Manager du club** (Brevo + Metricool), extrait en JSON dans `data/fanbase_cockpit.json` : indice de pénétration du bassin, catégories CMON_SCORE, campagnes e-mail, audiences sociales.
3. En option, Sofascore (xG/xA, `moneyball/sofascore.py`) et API-Football (`moneyball/apifootball.py`). Sofascore répond 403 depuis cet environnement et API-Football demande une clé : **ne compte sur aucun des deux**, le modèle tourne sur Transfermarkt seul.

Chaque joueur reçoit trois notes sur 100 :
- **Sport** : production (buts + 0,7 passe) / 90 min ramenée en équivalent de la ligue de référence par un coefficient de niveau, rétrécie vers la médiane du poste quand l'échantillon est court (confiance = minutes ÷ (minutes + 900)), puis lue comme rang percentile parmi les joueurs du vivier au même poste ayant ≥ 900 minutes ; plus disponibilité (minutes/saison ÷ 2 400), discipline, blessures. Gardiens : disponibilité + discipline seulement.
- **Valeur** : 40 % sous-évaluation (résidu d'une régression log(valeur) ~ sport + âge + âge² + ligue + poste, résolue par équations normales sans numpy), 25 % levier contractuel (≤ 6 mois = libre), 15 % tendance de valeur 12 mois, 20 % plus-value nette à 24 mois (valeur projetée par courbe d'âge par ligne — pic 26 ans MID/ATT, 27 DEF, 29 GK — × élan 12 mois, moins l'indemnité estimée, rapportée à la valeur actuelle).
- **Fan fit** : ancrage régional (bassin > canton > Romandie > Suisse > frontalier > international), francophonie, fidélité (durée moyenne par club), potentiel média (compte Instagram + spectacle offensif), lien club (ancien du club > foot suisse > foot francophone). **Les poids de ces cinq critères sont dérivés du cockpit** dans `fan_dna.py` : la pénétration du bassin renforce l'ancrage, la part de New Leads renforce la fidélité, l'ouverture des campagnes « recrues » par rapport à la moyenne renforce le média.

**Note finale** = (0,45 Sport + 0,30 Valeur + 0,25 Fan) × (0,85 + 0,30 × besoin du poste), le besoin par ligne venant de l'effectif réel (sous-effectif vs cible, contrats < 12 mois, joueurs ≥ 31 ans).

Le tableau de bord (`dashboard_template.html`, données injectées à la place de `/*__DATA__*/null`) montre l'ADN fan, les besoins, un plan mercato dans une enveloppe, et un tableau paginé avec curseurs de poids, filtres et fiche détaillée par joueur.

## Objectif

Produire `lausanne-moneyball/` : même moteur, paramètres LS, vivier réel, tableau de bord publié, sur une branche `claude/lausanne-moneyball-system`.

## Étapes

### 1. Refactoriser en configuration de club (une fois, profite aux deux clubs)

Avant de dupliquer quoi que ce soit, sors du code ce qui est propre à Yverdon dans un module `moneyball/club.py` chargé depuis un fichier `club.json` à la racine du dossier du club :

- `name`, `short` (« Yverdon Sport » / « FC Lausanne-Sport »), `search_name` pour Transfermarkt (`find_yverdon` devient `find_club`), `transfermarkt_club_id` optionnel ;
- `home_league` (code Transfermarkt : `C2` pour Yverdon, **`C1` pour Lausanne**) : c'est la ligue de référence du coefficient 1,0 dans `LEAGUE_COEF` et de l'équivalent de production ; renormalise les coefficients en conséquence (Super League = 1,0 → Challenge League ≈ 0,69, Ligue 2 ≈ 1,0, Ligue 1 ≈ 1,45, Belgique D1 ≈ 1,17, Autriche D1 ≈ 0,93, Eredivisie ≈ 1,24, Liga Portugal ≈ 1,03) ;
- `squad_target` par ligne (Yverdon 3/8/8/6 ; LS en Super League avec coupe d'Europe possible : **3/9/9/7**) ;
- géographie du fan fit : `bassin` (villes du cœur), `canton` (villes du canton), `region` (villes de Romandie), `frontalier`, et `club_needles` (chaînes reconnaissant le club dans transferts et clubs formateurs : `["lausanne-sport", "lausanne sport", "fc lausanne", "team vaud"]` — attention à ne pas capter « Stade Lausanne-Ouchy », exclure explicitement `"ouchy"`) ;
- libellés du tableau de bord (titre, eyebrow, ligue de référence, nom du bassin) et textes de `fan_dna.rationale`.

Yverdon doit tourner à l'identique après ce refactoring : relance `python -m moneyball.recommend` dans `yverdon-moneyball/` et vérifie que `data/recommendations.json` ne change que par les champs de configuration ; les 30 tests doivent passer.

### 2. Cockpit Fanbase Manager LS

L'artefact « Fanbase Manager LS » existe : https://claude.ai/artifact/NegSt5amoUin9MLXCoJ5ZQ. Lis-le avec l'outil Artifact (action `read`), extrais le bloc `<script id="payload" type="application/json">` et écris `lausanne-moneyball/data/fanbase_cockpit.json` **au même schéma** que celui d'Yverdon (clés `genere`, `series`, `game`, `penetration`, `brevo`, `croissance12m`, `campagnes`, `postsM`, `postsF`). Si le payload LS a une forme différente, adapte `fan_dna.load_cockpit` avec un mappage, pas le modèle. Si une clé manque (par ex. pas de campagne « recrues »), `derive_fan_dna` doit retomber sur le multiplicateur 1,0 et le dire dans `rationale`. Note dans le README la date du relevé et le bassin utilisé par le cockpit (a priori Lausanne + Ouest lausannois + Lavaux-Oron + Gros-de-Vaud ; reprends exactement ce que dit le cockpit).

### 3. `lausanne-moneyball/club.json`

```json
{
  "name": "FC Lausanne-Sport", "short": "LS", "search_name": "Lausanne-Sport",
  "home_league": "C1",
  "squad_target": {"GK": 3, "DEF": 9, "MID": 9, "ATT": 7},
  "club_needles": ["lausanne-sport", "lausanne sport", "fc lausanne", "team vaud"],
  "club_exclude": ["ouchy"],
  "bassin": ["lausanne", "renens", "prilly", "pully", "ecublens", "crissier", "bussigny", "chavannes-près-renens",
             "epalinges", "le mont-sur-lausanne", "lutry", "morges", "cully", "cheseaux", "romanel", "belmont-sur-lausanne"],
  "canton": ["vevey", "montreux", "nyon", "yverdon-les-bains", "aigle", "bex", "gland", "rolle", "cossonay",
             "oron", "villeneuve", "coppet", "payerne", "orbe", "moudon", "la tour-de-peilz", "echallens"],
  "region": ["genève", "geneve", "geneva", "fribourg", "neuchâtel", "neuchatel", "la chaux-de-fonds", "le locle",
             "sion", "sierre", "martigny", "monthey", "bulle", "delémont", "delemont", "porrentruy", "bienne", "biel",
             "carouge", "meyrin", "vernier", "lancy", "onex", "romont", "morat", "colombier", "boudry"],
  "frontalier": ["évian-les-bains", "evian", "thonon-les-bains", "annemasse", "annecy", "pontarlier", "besançon"],
  "labels": {"league_ref": "Super League", "bassin": "Lausanne et Ouest lausannois"}
}
```

Complète les listes de villes avec les communes que le cockpit LS nomme dans son bassin.

### 4. Vivier LS

Périmètre adapté à un club de Super League : championnats `C1 C2 FR2 FR3 BE1 A1 NL2 PO1` (vérifie chaque code avec la recherche de compétitions Transfermarkt avant de lancer ; si un code est faux, cherche par nom), âge 17-29, valeur ≤ **4 M€**. Exclus les joueurs de LS lui-même du vivier mais garde-le comme « ancien club » pour les partants. Le scan direct de Transfermarkt prend environ une heure par millier de joueurs (cadence ≈ 1 requête/s, captcha réessayé) : lance-le en arrière-plan, surveille le journal, ne le relance pas de zéro si ça coupe (le cache disque de 72 h reprend là où c'était).

```bash
cd lausanne-moneyball
python -m moneyball.scout --competitions C1 C2 FR2 FR3 BE1 A1 NL2 PO1 --max-value 4000000 --max-age 29
python -m moneyball.recommend
python -m unittest discover -s tests -t .
```

### 5. Contrôles avant publication

- L'effectif LS lu sur Transfermarkt fait 24-30 joueurs et contient des noms que tu peux vérifier sur la page du club.
- `pool_baselines` doit avoir ≥ 20 joueurs par ligne (sinon élargis les championnats).
- Affiche les 5 premiers par ligne et regarde-les avec un œil de recruteur : un joueur à 2 buts en 150 minutes ne doit pas être en tête ; les « Enfant du pays » doivent être nés dans les villes de `bassin` ou `canton` ; « Retour au club » ne doit désigner que d'anciens joueurs de LS, pas de Stade Lausanne-Ouchy.
- Aucune donnée inventée : si Transfermarkt bloque, dis-le et arrête-toi.

### 6. Livraison

- Tableau de bord : publie `lausanne-moneyball/dashboard.html` comme **nouvel** artefact (titre `Moneyball Lausanne-Sport`, icône `target`), ne touche pas à l'artefact d'Yverdon.
- Commite données (`data/candidates.json`, `data/squad.json`, `data/recommendations.json`, `data/fanbase_cockpit.json`, `dashboard.html`, jamais `data/cache/`) et code sur `claude/lausanne-moneyball-system`, pousse. Pas de pull request.
- README de `lausanne-moneyball/` : périmètre, date du relevé cockpit, date du scan, limites (pas de xG, équipe masculine, indemnité indicative, part de dates de naissance dans Brevo).
- Termine par un résumé en français : taille du vivier, besoins par ligne, top 3 par ligne avec la raison, différences de poids fan fit entre LS et Yverdon et pourquoi (ce que le cockpit LS dit de différent), ce qui a dû être corrigé.

## Contraintes

Équipe masculine uniquement. Français dans tous les textes visibles. Pas de nouvelle dépendance Python (PyPI est parfois injoignable dans l'environnement). Ne modifie pas `yverdon-moneyball/data/`. Si une étape échoue durablement, explique-le plutôt que de retomber sur le vivier de démonstration sans le signaler.
