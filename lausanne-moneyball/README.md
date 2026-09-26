# Moneyball LS — recrutement data pour le FC Lausanne-Sport

Même moteur que `../yverdon-moneyball/` (le paquet `moneyball/` et `dashboard_template.html` sont identiques), paramétré pour le FC Lausanne-Sport par `club.json`. Équipe masculine uniquement.

## Statut au 26.09.2026

| Étape | État |
|---|---|
| Configuration du club (`club.json`) | faite |
| Cockpit Fanbase Manager LS → `data/fanbase_cockpit.json` | importé (relevé du 16.09.2026), **non versionné** : dépôt public, volumes Brevo confidentiels |
| Vivier Transfermarkt (`data/squad.json`, `data/candidates.json`) | **fait** : scan du 26.09.2026, 2 860 joueurs, effectif LS de 28 joueurs |
| Recommandations et tableau de bord (`data/recommendations.json`, `dashboard.html`) | générés en local, **non versionnés** (signaux du cockpit), publiés en artefact privé « Moneyball Lausanne-Sport » |

Le scan a tourné le 26.09.2026 de 11 h 20 à 15 h 13 UTC dans l'environnement cloud « C'mon Sports » de Claude Code : 14 646 requêtes Transfermarkt, dont 4 172 captchas réessayés, 126 clubs sur les huit championnats. Depuis un poste hors du cloud, Transfermarkt servait un défi anti-robot à chaque requête : il n'a pas été contourné, `python -m moneyball.scout` s'y arrête avec un message explicite et n'écrit rien.

## Périmètre prévu

- **Ligue de référence** : Super League (`home_league` = `C1`). Les coefficients de niveau sont renormalisés : Super League 1,00 · Challenge League 0,69 · Ligue 2 1,00 · National 0,69 · Ligue 1 1,45 · Belgique D1 1,17 · Autriche D1 0,93 · Eredivisie 1,24 · Eerste Divisie 0,76 · Liga Portugal 1,03. Le facteur « niveau » du score Sport reste ancré sur la Super League pour les deux clubs.
- **Effectif cible** : 3 gardiens, 9 défenseurs, 9 milieux, 7 attaquants (Super League avec coupe d'Europe possible).
- **Championnats** : `C1 C2 FR2 FR3 BE1 A1 NL2 PO1`, joueurs de 17 à 29 ans valant au plus 4 M€ (défauts de `club.json` → `scout`). Noms lus par le scan : Super League (12 clubs), Challenge League (10), Ligue 2 (18), Ligue 3 (18, l'ancien National), Jupiler Pro League (18), Bundesliga autrichienne (12), Keuken Kampioen Divisie (20 ; `NL2` est l'Eerste Divisie, `NL1` l'Eredivisie), Liga Portugal (18).
- **Exclusions** : les joueurs du LS ne sont pas dans le vivier, ni ceux qu'il a prêtés ailleurs ou déjà engagés pour plus tard. Le LS reste reconnu comme « ancien club » : un ancien joueur reçoit le lien club maximal et le tag « Retour au club ».
- **Reconnaissance du club** : `lausanne-sport`, `lausanne sport`, `fc lausanne`, `team vaud`, en excluant tout nom contenant `ouchy` (Stade Lausanne-Ouchy n'est pas le LS).

## ADN fan tiré du cockpit

Source : artefact « Fanbase Manager LS » (claude.ai/artifact/NegSt5amoUin9MLXCoJ5ZQ), lu le 26.09.2026 ; **relevé Brevo du 16.09.2026, Metricool du 15.09.2026** (fenêtre du 18.06 au 15.09.2026). Le payload de cet artefact n'a pas la forme de celui d'Yverdon (`window.__FBM__` au lieu d'un bloc `<script id="payload">`) : `fan_dna.from_fbm_payload` le ramène au schéma du moteur. Le panel des autres clubs n'est pas repris.

Le fichier `data/fanbase_cockpit.json` n'est pas versionné, car ce dépôt est public et le cockpit contient les volumes Brevo du club. Pour le recréer, enregistrer le HTML de l'artefact (outil Artifact, action `read`), puis :

```bash
python -m moneyball.fan_dna --import-artifact artefact.html --source "Artefact Fanbase Manager LS, relevé du …"
```

Les chiffres ci-dessous sont volontairement qualitatifs : ils figurent dans le cockpit et dans le tableau de bord généré.

**Bassin utilisé par le cockpit : le canton de Vaud** (population OFS fin 2024 × 34 % de suiveurs actifs de football), où l'indice de pénétration est dans la bande « correcte » (18-24 %). Lecture secondaire : l'**agglomération lausannoise** (≈ 420 000 habitants, estimation du cockpit, pas un périmètre OFS), où l'indice dépasse le garde-fou de 40 %, ce qui signale un bassin réel plus large que l'agglomération. Le cockpit ne nomme aucune commune. Dans le fan fit, le « bassin » est donc l'agglomération lausannoise (Lausanne, Ouest lausannois, Pully-Lutry, Morges et communes voisines, liste dans `club.json`) et le « canton » le reste du canton de Vaud.

| Signal cockpit (16.09.2026) | Lecture | Effet sur le fan fit |
|---|---|---|
| Indice de pénétration du bassin | au-dessus du seuil de 18 %, dans la bande « correcte » | poids **Ancrage** renforcé, modérément |
| Catégories CMON_SCORE | tout le fichier en New Lead, mais le cockpit juge le scoring non calibré (score maximal sous le palier Warm de 25) | poids **Fidélité** laissé à 1,0 : signal mécanique, pas un comportement des fans |
| Annonce des recrues vs moyenne | la newsletter `NL_JOUEURS_FREDRICSON_CORDIER` (31.07.2026) ouvre à peine mieux que la moyenne des campagnes des 90 derniers jours | poids **Média** quasi inchangé |
| Langue des fans | pas d'attribut LANGUE dans Brevo | **Francophonie** au poids de base |

Ordre des poids : ancrage d'abord (environ un tiers), puis média et fidélité, puis francophonie et lien club. Les valeurs exactes et leurs justifications chiffrées sont dans le tableau de bord (section « L'ADN fan ») et se recalculent à chaque réimport du cockpit.

La campagne « recrues » est repérée par le mot-clé `NL_JOUEURS` (`recruit_campaign_keywords`) : la newsletter du 31.07.2026 est consacrée à Tyler Fredricson et Thomas Cordier, présentés comme recrues sur LinkedIn les 30 et 31.07. La moyenne d'ouverture est celle que calcule le cockpit sur toutes les campagnes des 90 derniers jours, car le payload ne liste que les dix plus gros envois.

## Relancer le scan (environnement où Transfermarkt répond)

Python 3.9+, aucune dépendance.

```bash
cd lausanne-moneyball
python -m moneyball.scout          # périmètre de club.json ; ≈ 1 h par millier de joueurs, cache 72 h dans data/cache/
python -m moneyball.recommend
python -m unittest discover -s tests -t .
```

Si le scan coupe, le relancer tel quel : le cache disque reprend là où il s'était arrêté. Avant de publier, contrôler :

- l'effectif LS lu (`data/squad.json`) compte 24 à 30 joueurs, avec des noms vérifiables sur la page du club ;
- `pool_baselines` compte au moins 20 joueurs par ligne (sinon élargir les championnats) ;
- en tête de chaque ligne, pas de joueur à gros ratio sur un petit échantillon ; les « Enfant du pays » sont nés dans une ville de `bassin` ou `canton` ; « Retour au club » ne désigne que d'anciens joueurs du LS.

Ne pas publier `python -m moneyball.recommend --demo` : ce vivier est fictif.

`data/recommendations.json` et `dashboard.html` embarquent les signaux du cockpit (contacts exploitables, indice de pénétration) : ne pas les pousser dans ce dépôt tant qu'il est public. `data/squad.json` et `data/candidates.json` ne contiennent que des données Transfermarkt.

## Fichiers

```
club.json                   configuration LS : ligue de référence, effectif cible, villes, libellés, textes
data/fanbase_cockpit.json   cockpit LS au schéma du moteur (non versionné, à réimporter)
moneyball/, tests/, dashboard_template.html   moteur commun (identique à yverdon-moneyball)
```

## Correction apportée au vu du vivier LS

Au premier calcul, un ailier remplaçant (630 minutes en deux saisons, confiance 0,41) sortait en tête des attaquants, et cinq petits échantillons figuraient dans le top 50. La production était rétrécie vers la médiane *en valeur* puis lue comme rang ; or la distribution est serrée autour de la médiane, si bien qu'une production gonflée par des bouts de match restait au 85e rang. Le rang de production est désormais lu sur la production observée puis rétréci vers le rang médian (0,5) selon la confiance (minutes ÷ (minutes + 900)). Il ne reste qu'un petit échantillon dans le top 50, deuxième des attaquants avec son tag « Échantillon faible ». Contrepartie : les gros producteurs perdent quelques points de rang, au profit des profils réguliers. Le moteur étant commun, Yverdon suivra au prochain calcul.

## Limites

- Pas de xG ni de note de match : Sofascore répond 403 hors navigateur et API-Football demande une clé. La note Sport repose sur buts, passes, minutes et discipline Transfermarkt.
- Note Sport des gardiens réduite à la disponibilité et à la discipline : plusieurs gardiens titulaires atteignent 95-100. Le besoin du LS au poste étant faible, ils restent derrière les milieux dans le classement pondéré ; une note propre aux gardiens est en chantier côté Yverdon.
- Un joueur arrivé d'un championnat sans coefficient (Japon, par exemple) n'est noté que sur ses minutes dans le périmètre.
- Équipe masculine uniquement.
- L'indemnité estimée est un ordre de grandeur, pas une offre.
- Date de naissance renseignée pour 69 % des contacts exploitables (`AX_BIRTHDATE`, alimenté par Arenametrix ; 5,8 % dans le champ Brevo natif) : l'âge des fans est mesurable mais pas encore modélisé.
- CMON_SCORE non calibré : tant que l'échelle n'est pas revue, la fidélité reste au poids de base.
- Audience sociale cumulée sur quatre réseaux sans dédoublonnage ; TikTok relié à Metricool mais sans données.
- L'indice de pénétration est une métrique propriétaire C'mon Sports, directionnelle et non auditée ; les volumes de contacts du club sont confidentiels.
