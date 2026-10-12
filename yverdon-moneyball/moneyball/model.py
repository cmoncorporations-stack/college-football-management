"""Modèle Moneyball : trois scores sur 100 par joueur + un besoin par poste.

- SPORT   : production par 90 minutes lue comme rang percentile dans le vivier (par poste,
            en équivalent Challenge League), rétrécie vers la médiane quand l'échantillon
            est court, plus disponibilité et discipline.
- VALEUR  : sous-évaluation (régression avec effets ligue et poste), levier contractuel,
            tendance de la valeur et plus-value nette attendue à 24 mois (courbe d'âge).
- FAN FIT : adéquation avec la fanbase, pondérée par l'ADN fan (fan_dna.py).

Score final = (wS·SPORT + wV·VALEUR + wF·FAN) × (0,85 + 0,30 × besoin du poste).

Depuis le 11.10.2026, la note Sport comporte une composante « dynamique de saison »
(season_dynamics) lue dans le journal match par match 2026/27 (matchlog.py) : part de
titularisations, part de minutes, tendance de rôle, forme sur 5 matchs, facteur gros matchs,
disponibilité et régularité. Elle pèse 25 % × confiance, confiance = matchs de l'équipe /
(matchs + 6) ; le reste garde la formule par sous-poste.

Entrée : joueurs au format normalisé produit par scout.py (ou demo.py) :
{id, name, position, age, citizenship[], birth_city, birth_country, market_value,
 mv_history[[date, value]], contract_expires, club, league_id, stats[{season,
 competition_id, appearances, goals, assists, minutes, yellow, red}],
 transfers[{date, from, to}], youth_clubs[], social_media[], injury_days}
"""
from __future__ import annotations

import math
from datetime import date

# Niveau relatif des championnats (Challenge League = référence 1.0).
# Codes compétition Transfermarkt.
LEAGUE_COEF = {
    "C1": 1.45,   # Super League (SUI)
    "C2": 1.00,   # Challenge League (SUI)
    "CHPL": 0.70, # Promotion League (SUI)
    "FR1": 2.10, "FR2": 1.45, "FR3": 1.00,  # Ligue 1, Ligue 2, National
    "BE1": 1.70, "BE2": 1.05,               # Belgique
    "L2": 1.75, "L3": 1.20,                 # Allemagne
    "A1": 1.35, "A2": 0.95,                 # Autriche
    "IT1": 2.10, "IT2": 1.60, "IT3": 0.90,  # Italie (Serie C : même valeur que le préfixe IT3A-C)
    "LUX1": 0.60,
    "NL1": 1.80, "NL2": 1.10, "PO1": 1.50, "PO2": 0.90, "ES1": 2.10, "ES2": 1.60,
    "GB1": 2.50, "GB2": 1.70, "PL1": 1.40, "DK1": 1.30, "DK2": 0.80, "SE1": 1.20, "NO1": 1.20,
    "RO1": 1.10, "BU1": 0.90, "MLS1": 1.40, "TR1": 1.60, "GR1": 1.40, "SER1": 1.00, "KR1": 1.10,
}
# Divisions inférieures et groupes régionaux : préfixe du code Transfermarkt → coefficient.
LEAGUE_PREFIX_COEF = (
    ("FR4", 0.65),   # National 2 (groupes A-C)
    ("FR5", 0.45),   # National 3
    ("CHC", 0.45),   # 1re Ligue Classic (groupes 1-3)
    ("CIR", 0.30),   # 2e Ligue interrégionale
    ("C19", 0.35),   # U19 Elite League suisse
    ("F19", 0.35),   # National U19 français
    ("GB21", 0.70),  # Premier League 2
    ("IT3", 0.90),   # Serie C
    ("E3G", 0.90),   # Primera Federación
    ("E4G", 0.60),   # Segunda Federación
    ("RL", 0.70),    # Regionalliga allemande
    ("BE3", 0.60),   # 1ste Nationale
    ("PT23", 0.60),  # Liga Next Gen U23
)
DEFAULT_COEF = 0.8
# Saison en cours, toutes compétitions : poids d'une minute de coupe nationale par rapport à une
# minute de championnat, niveau par défaut d'une minute de coupe d'Europe (ligue adverse inconnue),
# et montée en puissance de la saison en cours : poids = min(1, matchs de l'équipe / 15).
CUP_WEIGHT = 0.8
EURO_DEFAULT_COEF = 1.3
SEASON_RAMP_MATCHES = 15
# Composante dynamique : 25 % de la note Sport × confiance, confiance = matchs / (matchs + 6).
DYN_WEIGHT = 0.25
DYN_SHRINK_MATCHES = 6
CUP_WORDS = ("cup", "coupe", "pokal", "coppa", "copa", "trophy", "trophée")
EURO_WORDS = ("champions league", "europa league", "conference league", "uefa")
EURO_CODES = {"CL", "EL", "UCOL", "CLQ", "ELQ", "UCOLQ", "CLI", "ECLQ", "UEL", "UCL"}
SWISS_LEAGUES = {"C1", "C2", "CHPL", "CHC1", "CHC2", "CHC3", "CIR1", "CIR2", "C191", "S1PO"}
FRENCH_LANG_LEAGUES = {"FR1", "FR2", "FR3", "BE1", "BE2", "LUX1", "FR4A", "FR4B", "FR4C",
                       "FR5A", "FR5B", "FR5C", "FR5D", "FR5E", "FR5F", "FR5G", "FR5H", "F19B", "F19C", "F19F"}


def league_coef(competition_id: str | None) -> float | None:
    """Coefficient de niveau d'un championnat, None si inconnu (coupes, « Total », …)."""
    if not competition_id:
        return None
    if competition_id in LEAGUE_COEF:
        return LEAGUE_COEF[competition_id]
    for prefix, coef in LEAGUE_PREFIX_COEF:
        if competition_id.startswith(prefix):
            return coef
    return None

def competition_type(competition_id: str | None, label: str | None = None) -> str:
    """league (championnat à coefficient connu), national_cup, european ou other (« Total », amicaux…)."""
    lab = (label or "").lower()
    if competition_id and league_coef(competition_id) is not None:
        return "league"
    if (competition_id or "") in EURO_CODES or any(w in lab for w in EURO_WORDS):
        return "european"
    if any(w in lab for w in CUP_WORDS):
        return "national_cup"
    return "other"


def current_season_key(today: date | None = None) -> int:
    """Saison Transfermarkt en cours : 2026 pour 2026/27 (bascule au 1er juillet)."""
    t = today or date.today()
    return t.year if t.month >= 7 else t.year - 1


POSITION_GROUPS = {
    "GK": ("goalkeeper", "gardien"),
    "DEF": ("back", "defender", "défenseur", "defence", "sweeper"),
    "MID": ("midfield", "milieu"),
    "ATT": ("forward", "winger", "striker", "attack", "attaquant", "ailier"),
}
# Contribution attendue par 90 min (buts + 0,7 × passes) au niveau Challenge League.
EXPECTED_G_A_90 = {"GK": 0.0, "DEF": 0.10, "MID": 0.28, "ATT": 0.50}
# Effectif cible par ligne pour un club de Challenge League.
SQUAD_TARGET = {"GK": 3, "DEF": 8, "MID": 8, "ATT": 6}

# Géographie du bassin : voir geo.py (toponymes multilingues, commune → canton → zone,
# règle du club formateur). ROMAND_CLUBS y est défini.
from . import geo  # noqa: E402
ROMAND_CLUBS = geo.ROMAND_CLUBS
FRANCOPHONE = {
    "france", "belgium", "belgique", "switzerland", "suisse", "luxembourg", "monaco",
    "canada", "senegal", "sénégal", "cote d'ivoire", "côte d'ivoire", "ivory coast",
    "cameroon", "cameroun", "mali", "guinea", "guinée", "dr congo", "congo",
    "burkina faso", "benin", "bénin", "togo", "gabon", "niger", "chad", "tchad",
    "madagascar", "haiti", "haïti", "central african republic", "comoros", "djibouti",
    "morocco", "maroc", "algeria", "algérie", "tunisia", "tunisie", "mauritania",
    "burundi", "rwanda", "guadeloupe", "martinique", "french guiana", "reunion",
}


def position_group(position: str | None) -> str:
    p = (position or "").lower()
    for group, keys in POSITION_GROUPS.items():
        if any(k in p for k in keys):
            return group
    return "MID"


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _months_until(d: str | None, today: date) -> float | None:
    if not d:
        return None
    try:
        y, m, dd = (int(v) for v in d[:10].split("-"))
        return (date(y, m, dd) - today).days / 30.44
    except ValueError:
        return None


def _season_key(s: str) -> int:
    """'25/26' -> 2025 ; '2025' -> 2025 ; '2025/2026' -> 2025."""
    head = str(s).split("/")[0].strip()
    if not head.isdigit():
        return 0
    n = int(head)
    return n + 2000 if n < 100 else n


def recent_stats(p: dict, seasons: int = 2, today: date | None = None, team_matches: int | None = None) -> dict:
    """Agrège les deux dernières saisons, pondérées par le niveau et par la compétition.

    - Saisons passées : championnat seulement (compétitions à coefficient connu), poids 1.
    - Saison en cours : toutes compétitions. Championnat au poids 1,0 et à son coefficient ;
      coupe nationale au poids CUP_WEIGHT (0,8) et au niveau du championnat du joueur ;
      coupe d'Europe au poids 1,0 et au niveau EURO_DEFAULT_COEF (1,3, ligue adverse inconnue
      dans les agrégats). Le tout multiplié par le poids de saison min(1, matchs de l'équipe / 15)
      (`team_matches` : journal 2026/27, sinon classement de la ligne de championnat ; sans
      information, poids 1 comme avant).
    Si aucune ligne n'a de coefficient connu, toutes les lignes comptent au coefficient DEFAULT_COEF.

    `minutes`, `apps`, `g`, `a`, `cards` sont les totaux pondérés qui servent aux taux et à la
    confiance ; `minutes_raw`, `apps_raw`, `g_raw`, `a_raw` les totaux réels pour l'affichage.
    Les agrégats défensifs (gardien, équipe, médiane de ligue) lus par defence.py restent ceux
    des lignes de championnat, en minutes réelles.
    """
    rows = [r for r in p.get("stats", []) if r.get("minutes")]
    empty = {"minutes": 0, "apps": 0, "g": 0, "a": 0, "cards": 0, "coef": DEFAULT_COEF,
             "minutes_per_season": 0, "conceded": None, "clean_sheets": None, "gk_matches": 0,
             "gk_minutes": 0, "team_minutes": 0, "team_c90": None, "league_c90": None,
             "rank_score": None, "team_rows": 0, "minutes_raw": 0, "apps_raw": 0, "g_raw": 0, "a_raw": 0,
             "cup_minutes": 0, "euro_minutes": 0, "season_weight": None, "cur_minutes_raw": 0}
    if not rows:
        return empty
    keys = sorted({_season_key(r["season"]) for r in rows}, reverse=True)[:seasons]
    rows = [r for r in rows if _season_key(r["season"]) in keys]
    cur = current_season_key(today)

    def kind(r):
        return competition_type(r.get("competition_id"), r.get("competition"))

    league_rows = [r for r in rows if kind(r) == "league"]
    cur_other = [r for r in rows if _season_key(r["season"]) == cur and kind(r) in ("national_cup", "european")]
    fallback = not league_rows and not cur_other
    if fallback:
        league_rows = rows
    tm = team_matches or max((r.get("team_matches") or 0 for r in rows if _season_key(r["season"]) == cur), default=0)
    season_w = min(1.0, tm / SEASON_RAMP_MATCHES) if tm else 1.0
    cur_league = [r for r in league_rows if _season_key(r["season"]) == cur]
    cur_min = sum(r["minutes"] for r in cur_league)
    cur_level = (sum((league_coef(r.get("competition_id")) or DEFAULT_COEF) * r["minutes"] for r in cur_league) / cur_min
                 if cur_min else (league_coef(p.get("league_id")) or DEFAULT_COEF))
    weighted: list[tuple[dict, float, float]] = []
    for r in league_rows:
        w = season_w if _season_key(r["season"]) == cur else 1.0
        weighted.append((r, w, DEFAULT_COEF if fallback else (league_coef(r.get("competition_id")) or DEFAULT_COEF)))
    for r in cur_other:
        if kind(r) == "national_cup":
            weighted.append((r, season_w * CUP_WEIGHT, cur_level))
        else:
            weighted.append((r, season_w, EURO_DEFAULT_COEF))
    minutes = sum(r["minutes"] * w for r, w, _ in weighted)
    coef = (sum(level * r["minutes"] * w for r, w, level in weighted) / minutes) if minutes else DEFAULT_COEF
    used = [r for r, _, _ in weighted]
    st = {**empty,
          "minutes": minutes,
          "apps": sum(r.get("appearances", 0) * w for r, w, _ in weighted),
          "g": sum(r.get("goals", 0) * w for r, w, _ in weighted),
          "a": sum(r.get("assists", 0) * w for r, w, _ in weighted),
          "cards": sum((r.get("yellow", 0) + 3 * r.get("red", 0)) * w for r, w, _ in weighted),
          "coef": coef,
          "minutes_per_season": minutes / max(1, len(keys)),
          "minutes_raw": sum(r["minutes"] for r in used),
          "apps_raw": sum(r.get("appearances", 0) for r in used),
          "g_raw": sum(r.get("goals", 0) for r in used),
          "a_raw": sum(r.get("assists", 0) for r in used),
          "cup_minutes": sum(r["minutes"] for r in cur_other if kind(r) == "national_cup"),
          "euro_minutes": sum(r["minutes"] for r in cur_other if kind(r) == "european"),
          "season_weight": round(season_w, 3) if (cur_league or cur_other) else None,
          "cur_minutes_raw": cur_min + sum(r["minutes"] for r in cur_other)}
    # Gardien : buts encaissés et clean sheets (page « Clean sheets » Transfermarkt), championnat.
    gk_rows = [r for r in league_rows if r.get("conceded") is not None]
    if gk_rows:
        st["conceded"] = sum(r["conceded"] for r in gk_rows)
        st["clean_sheets"] = sum(r.get("clean_sheets") or 0 for r in gk_rows)
        st["gk_matches"] = sum(r.get("gk_matches") or r.get("appearances") or 0 for r in gk_rows)
        st["gk_minutes"] = sum(r["minutes"] for r in gk_rows)
        st["team_minutes"] = sum(90 * r["team_matches"] for r in gk_rows if r.get("team_matches"))
    # Équipe : buts encaissés par match, médiane de la ligue, rang (classement Transfermarkt).
    team_rows = [r for r in league_rows if r.get("team_matches") and r.get("team_conceded") is not None
                 and r.get("league_conceded_90_median")]
    if team_rows:
        w = sum(r["minutes"] for r in team_rows)
        st["team_c90"] = sum(r["team_conceded"] / r["team_matches"] * r["minutes"] for r in team_rows) / w
        st["league_c90"] = sum(r["league_conceded_90_median"] * r["minutes"] for r in team_rows) / w
        ranked = [r for r in team_rows if r.get("team_rank") and (r.get("team_count") or 0) > 1]
        if ranked:
            wr = sum(r["minutes"] for r in ranked)
            st["rank_score"] = sum((r["team_count"] - r["team_rank"]) / (r["team_count"] - 1) * r["minutes"]
                                   for r in ranked) / wr
        st["team_rows"] = len(team_rows)
    return st


# --------------------------------------------------------------------------- SPORT
SHRINK_MINUTES = 900     # en dessous, la production est tirée vers la médiane du poste
MIN_REF_MINUTES = 900    # minutes minimales pour entrer dans la distribution de référence
SOLE_STARTER_SHARE = 0.85  # part des minutes de l'équipe à partir de laquelle un gardien est « titulaire unique »


def sub_group(position: str | None) -> str:
    """Sous-poste de notation : GK, CB (central), FB (latéral), MID, ATT."""
    g = position_group(position)
    if g != "DEF":
        return g
    pos = (position or "").lower()
    return "FB" if ("left" in pos or "right" in pos or "wing" in pos) and "back" in pos else "CB"


def _ga90_c2(p: dict, st: dict | None = None) -> float:
    """Buts + 0,7 passe par 90 min, ramenés en équivalent Challenge League (× niveau)."""
    st = st or recent_stats(p)
    if not st["minutes"]:
        return 0.0
    return (st["g"] + 0.7 * st["a"]) / st["minutes"] * 90 * st["coef"]


def team_defence_index(st: dict) -> float | None:
    """Résultats défensifs de l'équipe : 0,5 = médiane de la ligue ; 1 = deux fois moins de buts
    encaissés par match que la médiane ; 0 = deux fois plus. None sans classement."""
    if not st.get("team_c90") or not st.get("league_c90"):
        return None
    return _clip(0.5 + (st["league_c90"] - st["team_c90"]) / st["league_c90"] / 2)


def gk_defence_index(st: dict) -> tuple[float | None, str]:
    """Indice défensif d'un gardien, sans production offensive.

    taux de clean sheets + terme « buts encaissés » :
    - gardien partageant le poste (< 85 % des minutes de l'équipe) : 1 − (ses buts encaissés/90
      ÷ ceux de l'équipe), borné à ± 0,5 ;
    - titulaire unique (le rapport vaut 1 par construction) : rang de l'équipe au classement,
      de −0,3 (dernier) à +0,3 (premier) ;
    - sans classement ni données d'équipe : 0, et la confiance est notée faible.
    Retourne (indice, mode) ; indice None sans donnée de buts encaissés.
    """
    if st.get("conceded") is None or not st.get("gk_minutes"):
        return None, "sans donnée de buts encaissés"
    gk_c90 = st["conceded"] / st["gk_minutes"] * 90
    cs_rate = st["clean_sheets"] / st["gk_matches"] if st.get("gk_matches") else 0.0
    if st.get("team_c90") and st.get("team_minutes"):
        share = st["gk_minutes"] / st["team_minutes"]
        if share < SOLE_STARTER_SHARE:
            return cs_rate + _clip(1 - gk_c90 / st["team_c90"], -0.5, 0.5), "gardien partagé : rapport à l'équipe"
        if st.get("rank_score") is not None:
            return cs_rate + 0.6 * (st["rank_score"] - 0.5), "titulaire unique : rang de l'équipe"
        return cs_rate, "titulaire unique sans classement"
    return cs_rate, "sans donnée d'équipe"


def pool_baselines(players: list[dict]) -> dict:
    """Distributions de référence par sous-poste, calculées sur le vivier (≥ MIN_REF_MINUTES).

    GK : indice défensif (gk_defence_index) ; CB : résultats défensifs de l'équipe
    (team_defence_index) ; FB : les deux listes (`def_values`) et la production offensive ;
    MID/ATT : production (équivalent C2). Chaque entrée : values triées, médiane, effectif.
    Remplace les attendus fixés à la main dès que le sous-poste compte 20 joueurs.
    """
    by_group: dict[str, list[float]] = {}
    for p in players:
        st = recent_stats(p)
        if st["minutes"] < MIN_REF_MINUTES:
            continue
        g = sub_group(p.get("position"))
        if g == "GK":
            idx, _ = gk_defence_index(st)
            if idx is not None:
                by_group.setdefault("GK", []).append(idx)
        elif g in ("CB", "FB"):
            d = team_defence_index(st)
            if d is not None:
                by_group.setdefault(g + ":def", []).append(d)
            if g == "FB":
                by_group.setdefault("FB", []).append(_ga90_c2(p, st))
        else:
            by_group.setdefault(g, []).append(_ga90_c2(p, st))
    out = {}
    for g, vals in by_group.items():
        vals.sort()
        entry = {"values": vals, "median": vals[len(vals) // 2], "n": len(vals),
                 "p90": vals[min(len(vals) - 1, int(len(vals) * 0.9))]}
        if g.endswith(":def"):
            base = g[:-4]
            out.setdefault(base, {"values": [], "median": 0.5, "n": 0})
            out[base].update({"def_values": vals, "def_median": entry["median"], "def_n": len(vals)})
        else:
            out.setdefault(g, {}).update(entry)
    return out


def _percentile(values: list[float], x: float) -> float:
    """Rang de x dans une liste triée (0 = plus bas, 1 = plus haut)."""
    if not values:
        return 0.5
    lo, hi = 0, len(values)
    while lo < hi:
        mid = (lo + hi) // 2
        if values[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo / len(values)


def _shrunk_rank(values: list[float] | None, median: float | None, x: float, confidence: float,
                 fallback: float) -> float:
    """Rang percentile de x, après rétrécissement vers la médiane selon la confiance ;
    `fallback` quand la distribution de référence est absente."""
    if not values:
        return fallback
    return _percentile(values, confidence * x + (1 - confidence) * median)


def _common(p: dict, st: dict) -> dict:
    level = _clip(st["coef"] / 1.45, 0.3, 1.2)       # Super League ≈ 1
    return {
        "level": level,
        "availability": _clip(st["minutes_per_season"] / 2400),
        "injury_penalty": _clip((p.get("injury_days") or 0) / 180, 0, 0.5),
        "discipline": _clip(1 - (st["cards"] / max(1, st["apps"])) / 0.5),
        "confidence": st["minutes"] / (st["minutes"] + SHRINK_MINUTES),   # 900 min → 0,5 ; 2700 → 0,75
        "g_a_90": (st["g"] + 0.7 * st["a"]) / st["minutes"] * 90 if st["minutes"] else 0.0,
    }


def _finish(raw: float, c: dict) -> float:
    raw = raw * (0.55 + 0.45 * c["level"]) * (1 - c["injury_penalty"])
    return round(100 * _clip(raw / 0.85), 1)


def _detail(st: dict, c: dict, confidence: float, note: str | None, dynamics: dict | None = None,
            w_dyn: float = 0.0, **extra) -> dict:
    d = {"g_a_90": round(c["g_a_90"], 2), "minutes_saison": round(st["minutes_per_season"]),
         "minutes_total": st["minutes_raw"], "minutes_ponderees": round(st["minutes"]),
         "confiance": round(confidence, 2), "confiance_note": note,
         "niveau": round(st["coef"], 2), "disponibilite": round(c["availability"], 2),
         "buts": st["g_raw"], "passes": st["a_raw"], "matchs": st["apps_raw"], "source": "transfermarkt",
         "minutes_coupe_2627": st["cup_minutes"], "minutes_europe_2627": st["euro_minutes"],
         "poids_saison_2627": st["season_weight"],
         "dynamique": dynamics, "poids_dynamique": round(w_dyn, 3)}
    d.update(extra)
    return d


def _rate(entries: list[dict], key: str = "prod") -> float | None:
    """Production (buts + 0,7 passes) ou buts encaissés par 90 minutes sur une liste de lignes de journal."""
    minutes = sum(e["minutes"] for e in entries)
    if not minutes:
        return None
    if key == "conceded":
        total = sum(e.get("conceded") or 0 for e in entries)
    else:
        total = sum(e["goals"] + 0.7 * e["assists"] for e in entries)
    return total / minutes * 90


def _ratio(num: float | None, den: float | None, cap: float = 3.0) -> float | None:
    if num is None or den is None:
        return None
    if den <= 0:
        return cap if num > 0 else None
    return min(cap, num / den)


def _prev_season_rate(p: dict, defensive: bool, today: date | None) -> tuple[float | None, int]:
    """Production (ou buts encaissés) par 90 min en championnat la saison précédente, et minutes."""
    prev = current_season_key(today) - 1
    rows = [r for r in p.get("stats", []) if r.get("minutes") and _season_key(r["season"]) == prev
            and competition_type(r.get("competition_id"), r.get("competition")) == "league"]
    if defensive:
        rows = [r for r in rows if r.get("conceded") is not None]
    minutes = sum(r["minutes"] for r in rows)
    if not minutes:
        return None, 0
    total = sum(r["conceded"] for r in rows) if defensive else sum(r.get("goals", 0) + 0.7 * r.get("assists", 0) for r in rows)
    return total / minutes * 90, minutes


def season_dynamics(p: dict, matchlog: dict | None, today: date | None = None,
                    standings: dict | None = None) -> dict | None:
    """Dynamique de saison d'un joueur, sur 2026/27 seulement, d'après le journal match par match.

    - starter_share : titularisations ÷ matchs de l'équipe où le joueur était disponible
      (hors blessure et suspension) ;
    - minutes_share : minutes jouées ÷ minutes possibles de l'équipe (90 par match, 120 après prolongation) ;
    - role_trend : part de minutes sur les 5 derniers matchs de l'équipe moins celle sur les 5 premiers
      (≥ 6 matchs) ; > 0 gagne sa place, < 0 la perd ;
    - form_5 : production (buts + 0,7 passes / 90) des 5 derniers matchs joués rapportée à celle de la
      saison ; gardiens et centraux : (buts encaissés/90 saison + 0,5) ÷ (buts encaissés/90 des 5 derniers
      + 0,5) — sur le terrain pour les centraux ; > 1 = mieux que sa moyenne ;
    - big_game_factor : même production contre les adversaires de la première moitié du classement du
      championnat au jour du journal ÷ contre la seconde moitié (gardiens, centraux : inversé sur les buts
      encaissés) ; matchs de coupe exclus (pas de classement) ;
    - availability_2627 : matchs manqués pour blessure ou suspension ÷ matchs de l'équipe ;
    - consistency : écart-type des minutes par match joué ÷ 45, borné à 1 (0 = toujours le même temps de jeu).
    Les matchs disputés avant l'arrivée du joueur au club (`other`) ne comptent pas.
    `score` (0-1) = 0,30 titularisation + 0,20 minutes + 0,15 tendance + 0,15 forme + 0,10 gros matchs
    + 0,05 disponibilité + 0,05 régularité (indicateur absent = 0,5) ; `confiance` = matchs / (matchs + 6).
    None sans journal.
    """
    log = ((matchlog or {}).get("players") or {}).get(p.get("id"))
    if not log or not log.get("entries"):
        return None
    entries = [e for e in log["entries"] if e.get("status") != "other"]
    if not entries:
        return None
    g = sub_group(p.get("position"))
    defensive = g in ("GK", "CB")
    n = len(entries)
    available = [e for e in entries if e["status"] not in ("injured", "suspended")]
    played = [e for e in entries if e["status"] == "played"]
    starts = sum(1 for e in played if e.get("started"))
    possible = sum(max(90, e["minutes"]) for e in entries)
    minutes = sum(e["minutes"] for e in played)
    starter_share = starts / len(available) if available else None
    minutes_share = minutes / possible if possible else None

    def share(block):
        poss = sum(max(90, e["minutes"]) for e in block)
        return sum(e["minutes"] for e in block) / poss if poss else 0.0

    role_trend = share(entries[-5:]) - share(entries[:5]) if n >= 6 else None
    key = "conceded" if defensive else "prod"
    season_rate = _rate(played, key)
    last5 = played[-5:]
    if defensive:
        form_5 = _ratio((season_rate or 0) + 0.5, (_rate(last5, key) or 0) + 0.5) if last5 else None
    else:
        form_5 = _ratio(_rate(last5, key), season_rate) if last5 else None
    big = None
    if standings:
        def half(e):
            st = standings.get(str(e.get("opponent_id")))
            if not st or not st.get("teams") or competition_type(e.get("competition_id")) != "league":
                return None
            return "top" if st["rank"] <= st["teams"] / 2 else "bottom"
        top = [e for e in played if half(e) == "top"]
        bottom = [e for e in played if half(e) == "bottom"]
        if top and bottom:
            if defensive:
                big = _ratio((_rate(bottom, key) or 0) + 0.5, (_rate(top, key) or 0) + 0.5)
            else:
                big = _ratio(_rate(top, key), _rate(bottom, key))
    missed = sum(1 for e in entries if e["status"] in ("injured", "suspended"))
    availability = missed / n
    consistency = None
    if len(played) >= 2:
        mins = [e["minutes"] for e in played]
        mean = sum(mins) / len(mins)
        consistency = min(1.0, (sum((m - mean) ** 2 for m in mins) / len(mins)) ** 0.5 / 45)
    neutral = lambda v: 0.5 if v is None else v  # noqa: E731
    score = (0.30 * neutral(starter_share) + 0.20 * neutral(minutes_share)
             + 0.15 * _clip(0.5 + (role_trend or 0.0)) + 0.15 * _clip(neutral(form_5) / 2 if form_5 is not None else 0.5)
             + 0.10 * _clip(neutral(big) / 2 if big is not None else 0.5)
             + 0.05 * (1 - availability) + 0.05 * (1 - neutral(consistency)))
    prev_rate, prev_minutes = _prev_season_rate(p, defensive, today)
    revelation = False
    if minutes >= 450 and prev_minutes > 0 and season_rate is not None and g != "CB":
        if defensive:
            revelation = season_rate <= prev_rate / 1.5
        else:
            revelation = season_rate >= 0.25 and season_rate >= 1.5 * prev_rate
    current = log.get("current_status") or None
    comps = sorted({e.get("competition_id") for e in entries if e.get("competition_id")})
    return {
        "starter_share": None if starter_share is None else round(starter_share, 3),
        "minutes_share": None if minutes_share is None else round(minutes_share, 3),
        "role_trend": None if role_trend is None else round(role_trend, 3),
        "form_5": None if form_5 is None else round(form_5, 2),
        "big_game_factor": None if big is None else round(big, 2),
        "availability_2627": round(availability, 3),
        "consistency": None if consistency is None else round(consistency, 3),
        "score": round(score, 3), "confiance": round(n / (n + DYN_SHRINK_MATCHES), 3),
        "matchs": n, "disponibles": len(available), "joues": len(played), "titularisations": starts,
        "minutes": minutes, "buts": sum(e["goals"] for e in played), "passes": sum(e["assists"] for e in played),
        "jaunes": sum(e["yellow"] for e in played), "rouges": sum(e["red"] for e in played),
        "buts_encaisses": sum(e.get("conceded") or 0 for e in played) if defensive else None,
        "production_90": None if season_rate is None or defensive else round(season_rate, 2),
        "encaisses_90": None if season_rate is None or not defensive else round(season_rate, 2),
        "production_90_2526": None if prev_rate is None else round(prev_rate, 2),
        "blesse": sum(1 for e in entries if e["status"] == "injured"),
        "suspendu": sum(1 for e in entries if e["status"] == "suspended"),
        "banc": sum(1 for e in entries if e["status"] == "bench"),
        "hors_groupe": sum(1 for e in entries if e["status"] == "not_in_squad"),
        "competitions": comps,
        "statut_actuel": current, "revelation": revelation,
        "derniers_matchs": [{k: e.get(k) for k in ("date", "competition_id", "opponent", "home", "result", "outcome",
                                                    "status", "started", "minutes", "goals", "assists", "conceded")}
                            for e in entries[-5:]],
    }


def _blend_dynamics(raw: float, dyn: dict | None) -> tuple[float, float]:
    """Mélange la note brute par sous-poste et la dynamique : poids = DYN_WEIGHT × confiance."""
    if not dyn:
        return raw, 0.0
    w = DYN_WEIGHT * dyn["confiance"]
    return (1 - w) * raw + w * dyn["score"], w


def sport_score_gk(p: dict, baselines: dict | None = None, dynamics: dict | None = None,
                   today: date | None = None) -> tuple[float, dict]:
    """Note Sport d'un gardien : jamais de production offensive.

    indice défensif (buts encaissés/90 rapportés à l'équipe, ou rang de l'équipe pour un
    titulaire unique, + taux de clean sheets) → rétréci vers la médiane des gardiens sous
    900 minutes → rang percentile entre gardiens → 65 % de la note ; disponibilité 25 % ;
    discipline 10 % ; puis coefficient de ligue et blessures. Sans donnée de buts encaissés :
    médiane des gardiens, confiance plafonnée à 0,5 et notée « faible ».
    """
    st = recent_stats(p, today=today, team_matches=(dynamics or {}).get("matchs"))
    c = _common(p, st)
    ref = (baselines or {}).get("GK") if (baselines or {}).get("GK", {}).get("n", 0) >= 20 else None
    idx, mode = gk_defence_index(st)
    confidence, note = c["confidence"], None
    if idx is None:
        rank, confidence, note = 0.5, min(confidence, 0.5), "faible"
    else:
        rank = _shrunk_rank(ref["values"] if ref else None, ref["median"] if ref else None, idx,
                            c["confidence"], _clip((idx + 0.3) / 1.0))
        if mode in ("titulaire unique sans classement", "sans donnée d'équipe"):
            confidence, note = min(confidence, 0.5), "faible"
    raw = 0.65 * rank + 0.25 * c["availability"] + 0.10 * c["discipline"]
    raw, w_dyn = _blend_dynamics(raw, dynamics)
    score = _finish(raw, c)
    detail = _detail(st, c, confidence, note, dynamics, w_dyn, rang_defensif=round(rank, 2), rang_production=None,
                     reference="vivier" if ref else "fixe", mode_gardien=mode,
                     buts_encaisses=st["conceded"], clean_sheets=st["clean_sheets"],
                     buts_encaisses_90=round(st["conceded"] / st["gk_minutes"] * 90, 2) if st.get("gk_minutes") else None,
                     equipe_encaisses_90=round(st["team_c90"], 2) if st.get("team_c90") else None,
                     ligue_encaisses_90=round(st["league_c90"], 2) if st.get("league_c90") else None,
                     rang_equipe=round(st["rank_score"], 2) if st.get("rank_score") is not None else None,
                     indice_defensif=round(idx, 3) if idx is not None else None)
    return score, detail


def sport_score_def(p: dict, baselines: dict | None = None, dynamics: dict | None = None,
                    today: date | None = None) -> tuple[float, dict]:
    """Note Sport d'un défenseur, par sous-poste.

    Central (CB) : résultats défensifs de l'équipe (buts encaissés/90 vs médiane de la ligue)
    → percentile entre centraux → 55 % ; disponibilité 30 % ; discipline 15 % (cartons en
    négatif) ; confiance plafonnée à 0,5 et notée « faible » (source Transfermarkt seule,
    aucune donnée individuelle de défense).
    Latéral (FB) : 50 % profil défensif (même formule) + 50 % production offensive (buts +
    0,7 passes, percentile entre latéraux) → 55 % ; disponibilité 35 % ; discipline 10 %.
    """
    st = recent_stats(p, today=today, team_matches=(dynamics or {}).get("matchs"))
    c = _common(p, st)
    g = sub_group(p.get("position"))
    b = (baselines or {}).get(g, {})
    d_idx = team_defence_index(st)
    ref_def = b.get("def_values") if b.get("def_n", 0) >= 20 else None
    if d_idx is None:
        rank_def, def_note = 0.5, "faible"
    else:
        rank_def = _shrunk_rank(ref_def, b.get("def_median"), d_idx, c["confidence"], d_idx)
        def_note = None
    if g == "CB":
        quality = rank_def
        raw = 0.55 * quality + 0.30 * c["availability"] + 0.15 * c["discipline"]
        confidence, note, rank_prod = min(c["confidence"], 0.5), "faible", None
    else:
        ref_off = b.get("values") if b.get("n", 0) >= 20 else None
        observed = c["g_a_90"] * st["coef"]
        rank_prod = _shrunk_rank(ref_off, b.get("median"), observed, c["confidence"],
                                 _clip(observed / EXPECTED_G_A_90["DEF"] / 1.3))
        quality = 0.5 * rank_def + 0.5 * rank_prod
        raw = 0.55 * quality + 0.35 * c["availability"] + 0.10 * c["discipline"]
        confidence, note = c["confidence"], def_note
        if note:
            confidence = min(confidence, 0.5)
    raw, w_dyn = _blend_dynamics(raw, dynamics)
    score = _finish(raw, c)
    detail = _detail(st, c, confidence, note, dynamics, w_dyn, sous_poste=g, rang_defensif=round(rank_def, 2),
                     rang_production=None if rank_prod is None else round(rank_prod, 2),
                     reference="vivier" if (ref_def or (g == "FB" and b.get("n", 0) >= 20)) else "fixe",
                     equipe_encaisses_90=round(st["team_c90"], 2) if st.get("team_c90") else None,
                     ligue_encaisses_90=round(st["league_c90"], 2) if st.get("league_c90") else None,
                     indice_defensif=None if d_idx is None else round(d_idx, 3))
    return score, detail


def sport_score(p: dict, baselines: dict | None = None, dynamics: dict | None = None,
                today: date | None = None) -> tuple[float, dict]:
    group = position_group(p.get("position"))
    if group == "GK":
        return sport_score_gk(p, baselines, dynamics, today)
    if group == "DEF":
        return sport_score_def(p, baselines, dynamics, today)
    st = recent_stats(p, today=today, team_matches=(dynamics or {}).get("matchs"))
    c = _common(p, st)
    availability, discipline, confidence, g_a_90 = c["availability"], c["discipline"], c["confidence"], c["g_a_90"]
    ref = (baselines or {}).get(group) if (baselines or {}).get(group, {}).get("n", 0) >= 20 else None

    # Bloc `perf` (API-Football ou Sofascore) : la note moyenne entre pour un tiers,
    # et la production attendue remplace les buts réels quand la source la donne
    # (xG + 0,7 xA, Sofascore) ; sinon buts + 0,7 passes + 0,1 passe clé, la passe
    # clé valant en moyenne un dixième de xA (API-Football).
    perf = p.get("perf") or p.get("sofascore") or {}
    xg_a_90 = None
    if perf.get("minutes"):
        if perf.get("xg") is not None:
            xg_a_90 = (perf["xg"] + 0.7 * (perf.get("xa") or 0)) / perf["minutes"] * 90
        elif perf.get("key_passes") is not None:
            xg_a_90 = ((perf.get("goals") or 0) + 0.7 * (perf.get("assists") or 0)
                       + 0.1 * perf["key_passes"]) / perf["minutes"] * 90
    rating = perf.get("rating")
    rating_score = _clip((rating - 6.0) / 1.6) if rating else None   # 6,0 → 0 ; 7,6 → 1

    prod_basis = xg_a_90 if xg_a_90 is not None else g_a_90
    if ref:
        # Production en équivalent C2, rétrécie vers la médiane du poste quand
        # l'échantillon est court, puis lue comme rang percentile dans le vivier.
        observed = prod_basis * st["coef"]
        shrunk = confidence * observed + (1 - confidence) * ref["median"]
        prod = _percentile(ref["values"], shrunk)
    else:
        prod = _clip(prod_basis * st["coef"] / EXPECTED_G_A_90[group] / 1.3)
    w_prod = {"MID": 0.45, "ATT": 0.60}[group]
    raw = w_prod * prod + (0.9 - w_prod) * availability + 0.10 * discipline
    if rating_score is not None:
        raw = 0.67 * raw + 0.33 * rating_score
    raw, w_dyn = _blend_dynamics(raw, dynamics)
    score = _finish(raw, c)
    detail = _detail(st, c, confidence, None, dynamics, w_dyn, rang_production=round(prod, 2),
                     reference="vivier" if ref else "fixe")
    if perf:
        detail.update({"source": "transfermarkt + " + (perf.get("source") or "sofascore"),
                       "xg_a_90": None if xg_a_90 is None else round(xg_a_90, 2),
                       "xg": perf.get("xg"), "xa": perf.get("xa"), "passes_cles": perf.get("key_passes"),
                       "note": rating, "saison_perf": perf.get("season")})
    return score, detail


# --------------------------------------------------------------------------- VALEUR
def _mv_trend(p: dict, today: date) -> float:
    hist = sorted((h for h in p.get("mv_history", []) if h[1]), key=lambda h: h[0])
    mv = p.get("market_value")
    if not hist or not mv:
        return 0.0
    cutoff = date(today.year - 1, today.month, min(today.day, 28)).isoformat()
    past = [h for h in hist if h[0] <= cutoff]
    base = past[-1][1] if past else hist[0][1]
    return (mv - base) / base if base else 0.0


def estimated_fee(p: dict, today: date) -> int:
    """Indemnité estimée : ≈0 à ≤ 6 mois de la fin de contrat, croît avec la durée restante."""
    mv = p.get("market_value") or 0
    months = _months_until(p.get("contract_expires"), today)
    if months is None:
        return int(mv * 0.8)
    if months <= 6:
        return 0
    return int(round(mv * (0.35 + 0.65 * _clip(months / 36)), -3))


# Âge de pic de valeur marchande par ligne ; la valeur monte avant, plafonne autour,
# puis décroît. Courbes usuelles du marché (gardiens plus tardifs, attaquants plus tôt).
PEAK_AGE = {"GK": 29, "DEF": 27, "MID": 26, "ATT": 26}


def age_growth_24m(group: str, age: int) -> float:
    """Variation attendue de la valeur marchande sur 24 mois, hors forme du moment."""
    d = age - PEAK_AGE.get(group, 27)
    if d <= -5:
        return 0.45
    if d <= -3:
        return 0.30
    if d <= -1:
        return 0.12
    if d <= 1:
        return 0.0
    if d <= 3:
        return -0.20
    return -0.40


def projected_value_24m(p: dict, today: date) -> int | None:
    """Valeur marchande projetée à 24 mois : courbe d'âge × élan de la valeur sur 12 mois."""
    mv = p.get("market_value")
    if not mv:
        return None
    group = position_group(p.get("position"))
    momentum = 1 + 0.5 * _clip(_mv_trend(p, today), -0.5, 0.5)
    return int(round(mv * (1 + age_growth_24m(group, p.get("age") or 27)) * momentum, -3))


def value_components(p: dict, today: date) -> dict:
    age = p.get("age") or 27
    months = _months_until(p.get("contract_expires"), today)
    if months is None:
        contract = 0.3
    elif months <= 6:
        contract = 1.0
    elif months <= 12:
        contract = 0.8
    elif months <= 18:
        contract = 0.5
    else:
        contract = 0.2
    resale = 1.0 if age <= 22 else 0.8 if age <= 24 else 0.55 if age <= 26 else 0.3 if age <= 29 else 0.1
    trend = _mv_trend(p, today)
    # Plus-value nette attendue : valeur projetée à 24 mois moins l'indemnité estimée,
    # rapportée à la valeur actuelle. 0 = on récupère sa mise ; +1 = on la double.
    mv = p.get("market_value")
    projected = projected_value_24m(p, today)
    fee = estimated_fee(p, today)
    if mv and projected is not None:
        plus_value = (projected - fee) / max(mv, 50_000)
        plus_value_score = _clip((plus_value + 0.5) / 2)
    else:
        plus_value, plus_value_score = None, 0.4
    return {"contrat": contract, "revente": resale, "tendance": trend,
            "tendance_score": _clip(0.5 + trend / 2),   # valeur doublée sur 12 mois → 1
            "mois_contrat": None if months is None else round(months, 1),
            "valeur_projetee_24m": projected, "plus_value": None if plus_value is None else round(plus_value, 2),
            "plus_value_score": plus_value_score, "cout_net": None if projected is None else fee - projected}


def _ols(X: list[list[float]], y: list[float]) -> list[float]:
    """Moindres carrés ordinaires par équations normales (Gauss avec pivot), sans numpy."""
    k = len(X[0])
    A = [[sum(r[i] * r[j] for r in X) for j in range(k)] for i in range(k)]
    b = [sum(r[i] * yi for r, yi in zip(X, y)) for i in range(k)]
    for i in range(k):
        A[i][i] += 1e-6                      # ridge minuscule : évite les colonnes vides
    for c in range(k):
        piv = max(range(c, k), key=lambda r: abs(A[r][c]))
        A[c], A[piv], b[c], b[piv] = A[piv], A[c], b[piv], b[c]
        for r in range(c + 1, k):
            f = A[r][c] / A[c][c]
            for j in range(c, k):
                A[r][j] -= f * A[c][j]
            b[r] -= f * b[c]
    beta = [0.0] * k
    for i in range(k - 1, -1, -1):
        beta[i] = (b[i] - sum(A[i][j] * beta[j] for j in range(i + 1, k))) / A[i][i]
    return beta


def value_regression(players: list[dict], sport: dict[str, float]) -> dict | None:
    """Régression log(valeur) ~ sport + âge + âge² + ligue + poste sur les joueurs à valeur connue.

    Retourne {"predict": f(joueur) -> valeur estimée (EUR), "resid": {id: résidu}} ou None
    si moins de 20 valeurs connues.
    """
    known = [p for p in players if p.get("market_value")]
    if len(known) < 20:
        return None
    leagues = sorted({p.get("league_id") or "?" for p in known})[1:]
    groups = ["DEF", "MID", "ATT"]

    def row(p):
        age = p.get("age") or 27
        g = position_group(p.get("position"))
        return ([1.0, sport[p["id"]] / 100, age, age * age / 100]
                + [1.0 if (p.get("league_id") or "?") == l else 0.0 for l in leagues]
                + [1.0 if g == gg else 0.0 for gg in groups])

    X = [row(p) for p in known]
    y = [math.log(p["market_value"]) for p in known]
    beta = _ols(X, y)
    resid = {p["id"]: yi - sum(b * x for b, x in zip(beta, r)) for p, r, yi in zip(known, X, y)}
    return {"predict": lambda p: int(round(math.exp(sum(b * x for b, x in zip(beta, row(p)))), -3)),
            "resid": resid, "n": len(known)}


def estimate_market_values(players: list[dict], sport: dict[str, float]) -> dict[str, int]:
    """Valeur marchande estimée par la régression pour les joueurs sans valeur Transfermarkt."""
    reg = value_regression(players, sport)
    if not reg:
        return {}
    return {p["id"]: reg["predict"](p) for p in players if not p.get("market_value")}


def undervaluation(players: list[dict], sport: dict[str, float]) -> dict[str, float]:
    """Régression log(valeur) ~ sport + âge + âge² + ligue + poste : résidu négatif = sous-évalué.

    Les effets fixes ligue et poste évitent qu'un joueur de National (valeurs
    médianes trois fois plus basses qu'en Super League) ou un gardien paraisse
    sous-évalué par construction. Retourne un score 0-1 (1 = le plus sous-évalué
    du vivier) ; un joueur sans valeur connue reçoit le rang du résidu nul (sa valeur
    estimée est, par construction, celle que la régression prédit).
    """
    reg = value_regression(players, sport)
    if not reg:
        return {p["id"]: 0.5 for p in players}
    resid = reg["resid"]
    order = sorted(resid, key=resid.get)
    n = len(order)
    out = {pid: round(1 - i / max(1, n - 1), 3) for i, pid in enumerate(order)}
    zero_rank = round(1 - sum(1 for v in resid.values() if v < 0) / max(1, n - 1), 3)
    for p in players:
        out.setdefault(p["id"], zero_rank)
    return out


# --------------------------------------------------------------------------- FAN FIT
def _has_played_for(p: dict, needle: str) -> bool:
    clubs = [t.get("from", "") for t in p.get("transfers", [])] + \
            [t.get("to", "") for t in p.get("transfers", [])] + list(p.get("youth_clubs", [])) + [p.get("club", "")]
    return any(needle in (c or "").lower() for c in clubs)


def fan_components(p: dict) -> dict:
    cits = [c.lower() for c in p.get("citizenship", [])]

    # Ancrage régional : lieu de naissance normalisé (geo.py) et club formateur, même règle pour tous.
    origine, ancrage = geo.zone(p.get("birth_city"), p.get("birth_country"), cits, p.get("youth_clubs", []))

    # Francophonie
    romand = origine in ("Bassin nord-vaudois", "Vaud", "Romandie")
    if romand or any(c in FRANCOPHONE and c not in ("switzerland", "suisse") for c in cits):
        franco = 1.0
    elif any(r.get("competition_id") in FRENCH_LANG_LEAGUES for r in p.get("stats", [])):
        franco = 0.6
    elif "switzerland" in cits:
        franco = 0.5
    else:
        franco = 0.15

    # Fidélité : durée moyenne passée par club (hors dernier passage en cours).
    dates = sorted(t["date"] for t in p.get("transfers", []) if t.get("date"))
    if len(dates) >= 2:
        span_y = (_season_key(dates[-1][:4]) - _season_key(dates[0][:4])) or 0.5
        tenure = span_y / (len(dates) - 1)
    else:
        tenure = 3.0
    fidelite = _clip(tenure / 3.0)

    # Potentiel média : présence sur les réseaux + spectacle offensif.
    social = p.get("social_media", [])
    presence = 1.0 if any("instagram" in s for s in social) else 0.6 if social else 0.2
    g_a_90 = recent_stats(p)
    ga = (g_a_90["g"] + 0.7 * g_a_90["a"]) / g_a_90["minutes"] * 90 if g_a_90["minutes"] else 0
    spectacle = _clip(ga / 0.5)
    media = 0.55 * presence + 0.45 * spectacle

    # Lien club
    if _has_played_for(p, "yverdon"):
        lien, lien_label = 1.0, "Ancien d'Yverdon"
    elif any(r.get("competition_id") in SWISS_LEAGUES for r in p.get("stats", [])):
        lien, lien_label = 0.6, "Foot suisse"
    elif any(r.get("competition_id") in FRENCH_LANG_LEAGUES for r in p.get("stats", [])):
        lien, lien_label = 0.3, "Foot francophone"
    else:
        lien, lien_label = 0.0, "—"

    return {"ancrage": ancrage, "francophonie": franco, "fidelite": round(fidelite, 2),
            "media": round(media, 2), "lien_club": lien,
            "_origine": origine, "_lien": lien_label, "_tenure": round(tenure, 1)}


# --------------------------------------------------------------------------- BESOINS
LONG_INJURY_MATCHES = 3   # matchs manqués consécutifs à partir desquels un titulaire blessé compte un demi-manque


def long_term_injured_starter(p: dict, matchlog: dict | None) -> bool:
    """Titulaire blessé longue durée : dernier statut `injured`, ≥ 3 matchs manqués consécutifs, et titulaire
    (≥ 50 % des matchs disponibles) avant la blessure — ou aucun match avant la blessure cette saison."""
    log = ((matchlog or {}).get("players") or {}).get(p.get("id"))
    if not log:
        return False
    entries = [e for e in log.get("entries", []) if e.get("status") != "other"]
    streak = 0
    for e in reversed(entries):
        if e["status"] != "injured":
            break
        streak += 1
    if streak < LONG_INJURY_MATCHES:
        return False
    before = entries[:len(entries) - streak]
    available = [e for e in before if e["status"] not in ("injured", "suspended")]
    if not available:
        return True
    return sum(1 for e in before if e.get("started")) / len(available) >= 0.5


def squad_needs(squad: list[dict], today: date, matchlog: dict | None = None) -> dict:
    """Besoin 0-1 par ligne : sous-effectif, contrats qui expirent, vieillissement.

    Avec le journal 2026/27, un titulaire blessé longue durée (≥ 3 matchs manqués consécutifs, dernier
    statut blessé) compte comme un demi-manque dans le sous-effectif.
    """
    needs = {}
    for group, target in SQUAD_TARGET.items():
        members = [p for p in squad if position_group(p.get("position")) == group]
        count = len(members)
        expiring = sum(1 for p in members if (m := _months_until(p.get("contract"), today)) is not None and m <= 12)
        veterans = sum(1 for p in members if (p.get("age") or 0) >= 31)
        long_injured = sum(1 for p in members if long_term_injured_starter(p, matchlog))
        shortage = _clip((target - (count - expiring - 0.5 * long_injured)) / target)
        ageing = veterans / count if count else 1.0
        need = _clip(0.6 * shortage + 0.25 * ageing + 0.15 * (expiring / count if count else 1))
        needs[group] = {"besoin": round(need, 2), "effectif": count, "cible": target,
                        "fin_contrat_12m": expiring, "31_ans_plus": veterans, "blesses_longue_duree": long_injured,
                        "age_moyen": round(sum(p.get("age") or 0 for p in members) / count, 1) if count else None}
    return needs


# --------------------------------------------------------------------------- ASSEMBLAGE
DEFAULT_WEIGHTS = {"sport": 0.45, "valeur": 0.30, "fan": 0.25}


def standings_from_defence(defence: dict | None, season: int) -> dict:
    """{club_id: {"rank", "teams", "competition_id"}} d'après data/defence.json pour une saison."""
    out = {}
    for key, block in (defence or {}).items():
        if int(block.get("season_id", 0)) != season:
            continue
        for t in block.get("table") or []:
            if t.get("club_id"):
                out[str(t["club_id"])] = {"rank": t["rank"], "teams": t.get("teams") or len(block["table"]),
                                          "competition_id": block.get("competition_id")}
    return out


def dynamic_tags(dyn: dict | None) -> list[str]:
    """Tags tirés de la dynamique de saison (journal 2026/27)."""
    if dyn is None:
        return ["Saison 26/27 non lue"]
    tags = []
    if dyn.get("form_5") is not None and dyn["form_5"] >= 1.3 and dyn["joues"] >= 3:
        tags.append("En forme")
    if dyn.get("role_trend") is not None:
        if dyn["role_trend"] <= -0.3:
            tags.append("Perd sa place")
        elif dyn["role_trend"] >= 0.3:
            tags.append("Gagne sa place")
    if dyn.get("starter_share") is not None and dyn["starter_share"] >= 0.8 and dyn["matchs"] >= 6:
        tags.append("Titulaire installé")
    if dyn.get("revelation"):
        tags.append("Révélation 26/27")
    if (dyn.get("statut_actuel") or {}).get("status") == "injured":
        tags.append("Blessé actuellement")
    return tags


def score_pool(players: list[dict], fan_weights: dict, needs: dict, today: date,
               weights: dict = DEFAULT_WEIGHTS, matchlog: dict | None = None, standings: dict | None = None) -> list[dict]:
    baselines = pool_baselines(players)
    sport, sport_detail, dynamics = {}, {}, {}
    for p in players:
        dynamics[p["id"]] = season_dynamics(p, matchlog, today, standings) if matchlog else None
        sport[p["id"]], sport_detail[p["id"]] = sport_score(p, baselines, dynamics[p["id"]], today)
    underval = undervaluation(players, sport)
    estimates = estimate_market_values(players, sport)

    out = []
    for raw_p in players:
        estimated = not raw_p.get("market_value") and raw_p["id"] in estimates
        # Valeur manquante : estimée par la régression, signalée comme telle, poids Valeur divisé par deux.
        # (sans historique : la tendance 12 mois d'une valeur estimée n'a pas de sens, elle vaut 0)
        p = {**raw_p, "market_value": estimates[raw_p["id"]], "mv_history": []} if estimated else raw_p
        vc = value_components(p, today)
        valeur = 100 * (0.40 * underval[p["id"]] + 0.25 * vc["contrat"] +
                        0.15 * vc["tendance_score"] + 0.20 * vc["plus_value_score"])
        fc = fan_components(p)
        fan = 100 * sum(fan_weights[k] * fc[k] for k in fan_weights)
        group = position_group(p.get("position"))
        need = needs.get(group, {}).get("besoin", 0.5)
        w_valeur = weights["valeur"] * (0.5 if estimated else 1.0)
        base = (weights["sport"] * sport[p["id"]] + w_valeur * valeur + weights["fan"] * fan) \
            / (weights["sport"] + w_valeur + weights["fan"])
        final = base * (0.85 + 0.30 * need)

        tags = []
        if vc["mois_contrat"] is not None and vc["mois_contrat"] <= 6:
            tags.append("Libre / pré-contrat")
        elif vc["mois_contrat"] is not None and vc["mois_contrat"] <= 12:
            tags.append("Fin de contrat < 12 mois")
        if underval[p["id"]] >= 0.8:
            tags.append("Sous-évalué")
        if vc["tendance"] >= 1.0:
            tags.append("Valeur doublée")
        elif vc["tendance"] <= -0.3:
            tags.append("Valeur en baisse")
        if fc["_origine"] in ("Bassin nord-vaudois", "Vaud"):
            tags.append("Enfant du pays")
        if sport_detail[p["id"]]["minutes_total"] < SHRINK_MINUTES:
            tags.append("Échantillon faible")
        if estimated:
            tags.append("Valeur estimée")
        elif not p.get("market_value"):
            tags.append("Valeur inconnue")
        if sport_detail[p["id"]].get("confiance_note") == "faible":
            tags.append("Confiance faible")
        if fc["_lien"] == "Ancien d'Yverdon":
            tags.append("Retour au club")
        if matchlog is not None:
            tags.extend(dynamic_tags(dynamics[p["id"]]))
        sofa = p.get("perf") or p.get("sofascore") or {}
        if sofa.get("xg") is not None and sofa.get("goals") is not None and (sofa.get("minutes") or 0) >= 900:
            # Moneyball : un joueur qui marque nettement moins que ses xG est une occasion,
            # un joueur qui marque nettement plus vit sur une saison de chance.
            if sofa["goals"] < 0.8 * sofa["xg"] - 1:
                tags.append("Sous-performe ses xG")
            elif sofa["goals"] > 1.3 * sofa["xg"] + 1:
                tags.append("Sur-performe ses xG")

        out.append({
            "id": p["id"], "name": p["name"], "position": p.get("position"), "groupe": group,
            "sous_poste": sub_group(p.get("position")),
            "age": p.get("age"), "club": p.get("club"), "league_id": p.get("league_id"),
            "nationalites": p.get("citizenship", []), "naissance": p.get("birth_city"),
            "market_value": p.get("market_value"), "market_value_estimated": estimated,
            "market_value_source": "regression" if estimated else ("transfermarkt" if p.get("market_value") else None),
            "indemnite_estimee": estimated_fee(p, today),
            "valeur_projetee_24m": vc["valeur_projetee_24m"],
            "contract_expires": p.get("contract_expires"), "url": p.get("url"),
            "scores": {"sport": round(sport[p["id"]], 1), "valeur": round(valeur, 1),
                       "fan": round(fan, 1), "besoin": need, "final": round(final, 1)},
            "detail": {"sport": sport_detail[p["id"]],
                       "valeur": {**{k: v for k, v in vc.items() if k not in ("tendance_score", "plus_value_score")},
                                  "sous_evaluation": underval[p["id"]],
                                  "tendance": round(vc["tendance"], 2)},
                       "fan": {k.lstrip("_"): v for k, v in fc.items()}},
            "tags": tags,
        })
    out.sort(key=lambda r: r["scores"]["final"], reverse=True)
    return out
