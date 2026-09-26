"""Modèle Moneyball : trois scores sur 100 par joueur + un besoin par poste.

- SPORT   : production par 90 minutes lue comme rang percentile dans le vivier (par poste,
            en équivalent Challenge League), rétrécie vers la médiane quand l'échantillon
            est court, plus disponibilité et discipline.
- VALEUR  : sous-évaluation (régression avec effets ligue et poste), levier contractuel,
            tendance de la valeur et plus-value nette attendue à 24 mois (courbe d'âge).
- FAN FIT : adéquation avec la fanbase, pondérée par l'ADN fan (fan_dna.py).

Score final = (wS·SPORT + wV·VALEUR + wF·FAN) × (0,85 + 0,30 × besoin du poste).

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
    "IT1": 2.10, "IT2": 1.60, "IT3": 1.00,  # Italie
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

# Géographie du bassin (indice de pénétration du cockpit : Jura-Nord vaudois + Broye-Vully).
BASSIN = {
    "yverdon-les-bains", "yverdon", "grandson", "orbe", "sainte-croix", "vallorbe",
    "yvonand", "chavornay", "baulmes", "champagne", "concise", "montagny-près-yverdon",
    "payerne", "avenches", "moudon", "lucens", "estavayer-le-lac", "estavayer",
    "cudrefin", "échallens", "echallens", "la sarraz", "romainmôtier", "le sentier",
}
VAUD = {
    "lausanne", "morges", "nyon", "vevey", "montreux", "renens", "pully", "prilly",
    "aigle", "bex", "gland", "rolle", "ecublens", "crissier", "bussigny", "epalinges",
    "la tour-de-peilz", "cossonay", "oron", "villeneuve", "coppet", "le mont-sur-lausanne",
}
ROMANDIE = {
    "genève", "geneve", "geneva", "fribourg", "neuchâtel", "neuchatel", "la chaux-de-fonds",
    "le locle", "sion", "sierre", "martigny", "monthey", "bulle", "delémont", "delemont",
    "porrentruy", "bienne", "biel/bienne", "biel", "carouge", "meyrin", "vernier",
    "lancy", "onex", "thônex", "romont", "morat", "murten", "colombier", "boudry",
}
ROMAND_CLUBS = ("yverdon", "lausanne", "servette", "sion", "xamax", "neuchâtel", "fribourg",
                "étoile carouge", "etoile carouge", "stade nyonnais", "nyon", "bulle",
                "la chaux-de-fonds", "stade lausanne", "ouchy", "meyrin", "bavois", "vevey",
                "echallens", "grandson", "delémont", "delemont", "monthey", "martigny")
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


def recent_stats(p: dict, seasons: int = 2) -> dict:
    """Agrège les deux dernières saisons, en championnat, pondérées par le niveau."""
    rows = [r for r in p.get("stats", []) if r.get("minutes")]
    if not rows:
        return {"minutes": 0, "apps": 0, "g": 0, "a": 0, "cards": 0, "coef": DEFAULT_COEF,
                "minutes_per_season": 0}
    keys = sorted({_season_key(r["season"]) for r in rows}, reverse=True)[:seasons]
    rows = [r for r in rows if _season_key(r["season"]) in keys]
    league_rows = [r for r in rows if league_coef(r.get("competition_id")) is not None] or rows
    minutes = sum(r["minutes"] for r in league_rows)
    coef = (sum((league_coef(r.get("competition_id")) or DEFAULT_COEF) * r["minutes"]
                for r in league_rows) / minutes) if minutes else DEFAULT_COEF
    return {
        "minutes": minutes,
        "apps": sum(r.get("appearances", 0) for r in league_rows),
        "g": sum(r.get("goals", 0) for r in league_rows),
        "a": sum(r.get("assists", 0) for r in league_rows),
        "cards": sum(r.get("yellow", 0) + 3 * r.get("red", 0) for r in league_rows),
        "coef": coef,
        "minutes_per_season": minutes / max(1, len(keys)),
    }


# --------------------------------------------------------------------------- SPORT
SHRINK_MINUTES = 900     # en dessous, la production est tirée vers la médiane du poste
MIN_REF_MINUTES = 900    # minutes minimales pour entrer dans la distribution de référence


def _ga90_c2(p: dict, st: dict | None = None) -> float:
    """Buts + 0,7 passe par 90 min, ramenés en équivalent Challenge League (× niveau)."""
    st = st or recent_stats(p)
    if not st["minutes"]:
        return 0.0
    return (st["g"] + 0.7 * st["a"]) / st["minutes"] * 90 * st["coef"]


def pool_baselines(players: list[dict]) -> dict:
    """Distribution de référence de la production par poste, calculée sur le vivier.

    Pour chaque groupe de poste : la liste triée des productions (équivalent C2) des
    joueurs ayant au moins MIN_REF_MINUTES minutes, leur médiane et leur effectif.
    Remplace les attendus fixés à la main dès que le groupe compte 20 joueurs.
    """
    by_group: dict[str, list[float]] = {}
    for p in players:
        st = recent_stats(p)
        if st["minutes"] >= MIN_REF_MINUTES:
            by_group.setdefault(position_group(p.get("position")), []).append(_ga90_c2(p, st))
    out = {}
    for g, vals in by_group.items():
        vals.sort()
        out[g] = {"values": vals, "median": vals[len(vals) // 2], "n": len(vals),
                  "p90": vals[min(len(vals) - 1, int(len(vals) * 0.9))]}
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


def sport_score(p: dict, baselines: dict | None = None) -> tuple[float, dict]:
    group = position_group(p.get("position"))
    st = recent_stats(p)
    level = _clip(st["coef"] / 1.45, 0.3, 1.2)       # Super League ≈ 1
    availability = _clip(st["minutes_per_season"] / 2400)
    injury_penalty = _clip((p.get("injury_days") or 0) / 180, 0, 0.5)
    g_a_90 = (st["g"] + 0.7 * st["a"]) / st["minutes"] * 90 if st["minutes"] else 0.0
    discipline = _clip(1 - (st["cards"] / max(1, st["apps"])) / 0.5)
    confidence = st["minutes"] / (st["minutes"] + SHRINK_MINUTES)   # 900 min → 0,5 ; 2700 → 0,75
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

    if group == "GK":
        raw = 0.75 * availability + 0.25 * discipline
        if rating_score is not None:
            raw = 0.5 * raw + 0.5 * rating_score
    else:
        prod_basis = xg_a_90 if xg_a_90 is not None else g_a_90
        if ref:
            # Production en équivalent C2, rétrécie vers la médiane du poste quand
            # l'échantillon est court, puis lue comme rang percentile dans le vivier.
            observed = prod_basis * st["coef"]
            shrunk = confidence * observed + (1 - confidence) * ref["median"]
            prod = _percentile(ref["values"], shrunk)
        else:
            prod = _clip(prod_basis * st["coef"] / EXPECTED_G_A_90[group] / 1.3)
        w_prod = {"DEF": 0.25, "MID": 0.45, "ATT": 0.60}[group]
        raw = w_prod * prod + (0.9 - w_prod) * availability + 0.10 * discipline
        if rating_score is not None:
            raw = 0.67 * raw + 0.33 * rating_score
    raw = raw * (0.55 + 0.45 * level) * (1 - injury_penalty)
    score = round(100 * _clip(raw / 0.85), 1)
    detail = {"g_a_90": round(g_a_90, 2), "minutes_saison": round(st["minutes_per_season"]),
              "minutes_total": st["minutes"], "confiance": round(confidence, 2),
              "rang_production": round(prod, 2) if group != "GK" else None,
              "reference": "vivier" if ref else "fixe",
              "niveau": round(st["coef"], 2), "disponibilite": round(availability, 2),
              "buts": st["g"], "passes": st["a"], "matchs": st["apps"], "source": "transfermarkt"}
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


def undervaluation(players: list[dict], sport: dict[str, float]) -> dict[str, float]:
    """Régression log(valeur) ~ sport + âge + âge² + ligue + poste : résidu négatif = sous-évalué.

    Les effets fixes ligue et poste évitent qu'un joueur de National (valeurs
    médianes trois fois plus basses qu'en Super League) ou un gardien paraisse
    sous-évalué par construction. Retourne un score 0-1 (1 = le plus sous-évalué
    du vivier) ; 0,5 pour les joueurs sans valeur marchande connue.
    """
    known = [p for p in players if p.get("market_value")]
    if len(known) < 20:
        return {p["id"]: 0.5 for p in players}
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
    order = sorted(resid, key=resid.get)
    n = len(order)
    out = {pid: round(1 - i / max(1, n - 1), 3) for i, pid in enumerate(order)}
    for p in players:
        out.setdefault(p["id"], 0.5)
    return out


# --------------------------------------------------------------------------- FAN FIT
def _has_played_for(p: dict, needle: str) -> bool:
    clubs = [t.get("from", "") for t in p.get("transfers", [])] + \
            [t.get("to", "") for t in p.get("transfers", [])] + list(p.get("youth_clubs", [])) + [p.get("club", "")]
    return any(needle in (c or "").lower() for c in clubs)


def fan_components(p: dict) -> dict:
    city = (p.get("birth_city") or "").strip().lower()
    country = (p.get("birth_country") or "").strip().lower()
    cits = [c.lower() for c in p.get("citizenship", [])]
    youth = " ".join(p.get("youth_clubs", [])).lower()

    # Ancrage régional
    if city in BASSIN or "yverdon" in youth:
        ancrage, origine = 1.0, "Bassin nord-vaudois"
    elif city in VAUD:
        ancrage, origine = 0.85, "Vaud"
    elif city in ROMANDIE or any(c in youth for c in ROMAND_CLUBS):
        ancrage, origine = 0.7, "Romandie"
    elif country in ("switzerland", "suisse") or "switzerland" in cits:
        ancrage, origine = 0.45, "Suisse"
    elif country == "france" and city in {"pontarlier", "annecy", "thonon-les-bains", "besançon",
                                           "besancon", "morteau", "annemasse", "évian-les-bains"}:
        ancrage, origine = 0.4, "Frontalier"
    else:
        ancrage, origine = 0.1, "International"

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
def squad_needs(squad: list[dict], today: date) -> dict:
    """Besoin 0-1 par ligne : sous-effectif, contrats qui expirent, vieillissement."""
    needs = {}
    for group, target in SQUAD_TARGET.items():
        members = [p for p in squad if position_group(p.get("position")) == group]
        count = len(members)
        expiring = sum(1 for p in members if (m := _months_until(p.get("contract"), today)) is not None and m <= 12)
        veterans = sum(1 for p in members if (p.get("age") or 0) >= 31)
        shortage = _clip((target - (count - expiring)) / target)
        ageing = veterans / count if count else 1.0
        need = _clip(0.6 * shortage + 0.25 * ageing + 0.15 * (expiring / count if count else 1))
        needs[group] = {"besoin": round(need, 2), "effectif": count, "cible": target,
                        "fin_contrat_12m": expiring, "31_ans_plus": veterans,
                        "age_moyen": round(sum(p.get("age") or 0 for p in members) / count, 1) if count else None}
    return needs


# --------------------------------------------------------------------------- ASSEMBLAGE
DEFAULT_WEIGHTS = {"sport": 0.45, "valeur": 0.30, "fan": 0.25}


def score_pool(players: list[dict], fan_weights: dict, needs: dict, today: date,
               weights: dict = DEFAULT_WEIGHTS) -> list[dict]:
    baselines = pool_baselines(players)
    sport, sport_detail = {}, {}
    for p in players:
        sport[p["id"]], sport_detail[p["id"]] = sport_score(p, baselines)
    underval = undervaluation(players, sport)

    out = []
    for p in players:
        vc = value_components(p, today)
        valeur = 100 * (0.40 * underval[p["id"]] + 0.25 * vc["contrat"] +
                        0.15 * vc["tendance_score"] + 0.20 * vc["plus_value_score"])
        fc = fan_components(p)
        fan = 100 * sum(fan_weights[k] * fc[k] for k in fan_weights)
        group = position_group(p.get("position"))
        need = needs.get(group, {}).get("besoin", 0.5)
        base = weights["sport"] * sport[p["id"]] + weights["valeur"] * valeur + weights["fan"] * fan
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
        if not p.get("market_value"):
            tags.append("Valeur inconnue")
        if fc["_lien"] == "Ancien d'Yverdon":
            tags.append("Retour au club")
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
            "age": p.get("age"), "club": p.get("club"), "league_id": p.get("league_id"),
            "nationalites": p.get("citizenship", []), "naissance": p.get("birth_city"),
            "market_value": p.get("market_value"), "indemnite_estimee": estimated_fee(p, today),
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
