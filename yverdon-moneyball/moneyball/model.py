"""Modèle Moneyball : trois scores sur 100 par joueur + un besoin par poste.

- SPORT   : production par 90 minutes et disponibilité, corrigées du niveau du championnat.
- VALEUR  : sous-évaluation par rapport au marché, tendance de la valeur, levier
            contractuel (fin de contrat = transfert bon marché) et potentiel de revente.
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
    "IT2": 1.60, "IT3": 1.00,               # Italie
    "LUX1": 0.60,
}
DEFAULT_COEF = 0.8

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
SWISS_LEAGUES = {"C1", "C2", "CHPL"}
FRENCH_LANG_LEAGUES = {"FR1", "FR2", "FR3", "BE1", "BE2", "LUX1"}


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
    league_rows = [r for r in rows if r.get("competition_id") in LEAGUE_COEF] or rows
    minutes = sum(r["minutes"] for r in league_rows)
    coef = (sum(LEAGUE_COEF.get(r.get("competition_id"), DEFAULT_COEF) * r["minutes"]
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
def sport_score(p: dict) -> tuple[float, dict]:
    group = position_group(p.get("position"))
    st = recent_stats(p)
    level = _clip(st["coef"] / 1.45, 0.3, 1.2)       # Super League ≈ 1
    availability = _clip(st["minutes_per_season"] / 2400)
    injury_penalty = _clip((p.get("injury_days") or 0) / 180, 0, 0.5)
    g_a_90 = (st["g"] + 0.7 * st["a"]) / st["minutes"] * 90 if st["minutes"] else 0.0
    discipline = _clip(1 - (st["cards"] / max(1, st["apps"])) / 0.5)

    if group == "GK":
        raw = 0.75 * availability + 0.25 * discipline
    else:
        prod = _clip(g_a_90 * st["coef"] / EXPECTED_G_A_90[group] / 1.3)
        w_prod = {"DEF": 0.25, "MID": 0.45, "ATT": 0.60}[group]
        raw = w_prod * prod + (0.9 - w_prod) * availability + 0.10 * discipline
    raw = raw * (0.55 + 0.45 * level) * (1 - injury_penalty)
    score = round(100 * _clip(raw / 0.85), 1)
    return score, {"g_a_90": round(g_a_90, 2), "minutes_saison": round(st["minutes_per_season"]),
                   "niveau": round(st["coef"], 2), "disponibilite": round(availability, 2),
                   "buts": st["g"], "passes": st["a"], "matchs": st["apps"]}


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
    return {"contrat": contract, "revente": resale, "tendance": trend,
            "tendance_score": _clip(0.5 + trend), "mois_contrat": None if months is None else round(months, 1)}


def undervaluation(players: list[dict], sport: dict[str, float]) -> dict[str, float]:
    """Régression log(valeur) ~ score sportif + âge sur le vivier : résidu négatif = sous-évalué.

    Retourne pour chaque joueur un score 0-1 (1 = le plus sous-évalué du vivier).
    """
    pts = [(p["id"], sport[p["id"]], p.get("age") or 27, math.log(max(25_000, p.get("market_value") or 25_000)))
           for p in players]
    n = len(pts)
    if n < 5:
        return {pid: 0.5 for pid, *_ in pts}
    # Moindres carrés à deux variables (score, âge) — forme normale résolue à la main.
    xs1 = [s for _, s, _, _ in pts]
    xs2 = [a for _, _, a, _ in pts]
    ys = [y for *_, y in pts]
    m1, m2, my = sum(xs1) / n, sum(xs2) / n, sum(ys) / n
    s11 = sum((x - m1) ** 2 for x in xs1)
    s22 = sum((x - m2) ** 2 for x in xs2)
    s12 = sum((a - m1) * (b - m2) for a, b in zip(xs1, xs2))
    s1y = sum((a - m1) * (y - my) for a, y in zip(xs1, ys))
    s2y = sum((b - m2) * (y - my) for b, y in zip(xs2, ys))
    det = s11 * s22 - s12 ** 2
    if abs(det) < 1e-9:
        b1, b2 = (s1y / s11 if s11 else 0.0), 0.0
    else:
        b1 = (s1y * s22 - s2y * s12) / det
        b2 = (s2y * s11 - s1y * s12) / det
    b0 = my - b1 * m1 - b2 * m2
    resid = {pid: y - (b0 + b1 * s + b2 * a) for pid, s, a, y in pts}
    # Rang percentile inversé : résidu le plus bas → 1.
    order = sorted(resid, key=resid.get)
    return {pid: round(1 - i / (n - 1), 3) for i, pid in enumerate(order)}


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
    sport, sport_detail = {}, {}
    for p in players:
        sport[p["id"]], sport_detail[p["id"]] = sport_score(p)
    underval = undervaluation(players, sport)

    out = []
    for p in players:
        vc = value_components(p, today)
        valeur = 100 * (0.40 * underval[p["id"]] + 0.25 * vc["contrat"] +
                        0.20 * vc["tendance_score"] + 0.15 * vc["revente"])
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
        if vc["tendance"] >= 0.3:
            tags.append("Valeur en hausse")
        if fc["_origine"] in ("Bassin nord-vaudois", "Vaud"):
            tags.append("Enfant du pays")
        if fc["_lien"] == "Ancien d'Yverdon":
            tags.append("Retour au club")

        out.append({
            "id": p["id"], "name": p["name"], "position": p.get("position"), "groupe": group,
            "age": p.get("age"), "club": p.get("club"), "league_id": p.get("league_id"),
            "nationalites": p.get("citizenship", []), "naissance": p.get("birth_city"),
            "market_value": p.get("market_value"), "indemnite_estimee": estimated_fee(p, today),
            "contract_expires": p.get("contract_expires"), "url": p.get("url"),
            "scores": {"sport": round(sport[p["id"]], 1), "valeur": round(valeur, 1),
                       "fan": round(fan, 1), "besoin": need, "final": round(final, 1)},
            "detail": {"sport": sport_detail[p["id"]],
                       "valeur": {**{k: v for k, v in vc.items() if k != "tendance_score"},
                                  "sous_evaluation": underval[p["id"]],
                                  "tendance": round(vc["tendance"], 2)},
                       "fan": {k.lstrip("_"): v for k, v in fc.items()}},
            "tags": tags,
        })
    out.sort(key=lambda r: r["scores"]["final"], reverse=True)
    return out
