"""Préparation de match d'Yverdon Sport : xG-tirs, notes d'équipes, pronostic, onze recommandé, projections.

    python -m moneyball.matchprep --today 2026-10-11      # → data/matchprep.json

Approche (inspirée des clubs pilotés par la donnée, comme l'AC Como ; le modèle interne de Como
n'est pas public, rien ici n'en reprend les paramètres) : quatre blocs transparents, tous calculés
sur des données Transfermarkt réelles de la saison 2026/27.

1. xG-tirs : Transfermarkt publie, par match, tirs totaux, tirs non cadrés et tirs arrêtés
   (`/spielbericht/statistik/spielbericht/{id}`) mais pas d'expected goals. On ajuste sur les
   matchs de Challenge League joués : buts ≈ a × tirs cadrés + b × tirs non cadrés (moindres
   carrés sans constante, tirs cadrés = arrêtés + buts de l'équipe). Ce n'est pas un xG fondé
   sur la position des tirs : c'est une mesure du volume et de la qualité de tir, moins bruitée
   que les buts.
2. Notes d'équipes : modèle de Poisson (attaque, défense, avantage du terrain) ajusté sur la
   Challenge League 2026/27, cible = 50 % buts + 50 % xG-tirs, rétréci vers la saison 2025/26
   (classement Transfermarkt, poids équivalent à PRIOR_MATCHES matchs ; 0 pour un club absent de
   la Challenge League 25/26).
3. Coupe contre un club amateur : régression de Poisson sur les matchs du 1er tour de Coupe de
   Suisse 2026/27 opposant un club de Super League ou de Challenge League à un club de division
   à coefficient connu : log λ = α + β × ln(coefficient de l'équipe / coefficient adverse)
   + h (amateur à domicile). L'adversaire est ensuite situé dans sa propre ligue (buts pour et
   contre par match rapportés à la moyenne de la ligue, rétrécis de 8 matchs).
4. Onze recommandé : joueurs disponibles au jour du journal (blessés et suspendus exclus),
   postes réellement occupés cette saison (compositions Transfermarkt), système le plus utilisé
   par l'entraîneur en place ; note de sélection = 0,45 note Sport du modèle + 0,35 part de
   minutes sur les 3 derniers matchs de l'équipe + 0,20 score de dynamique 26/27.
5. Projections : probabilités de buteur, et simulation Monte-Carlo de la fin de saison de
   Challenge League (calendrier réel, notes figées au jour du calcul).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
from collections import Counter
from datetime import date
from pathlib import Path

from .matchlog import MatchlogClient, competition_type
from .model import (LEAGUE_COEF, league_coef, pool_baselines, position_group, season_dynamics, sport_score,
                    standings_from_defence)
from .tm_direct import parse_html

DATA = Path(__file__).resolve().parent.parent / "data"
OUT = DATA / "matchprep.json"
YS = "322"
PRIOR_MATCHES = 6          # poids de la saison 25/26 dans les notes d'équipes, en matchs
OPP_SHRINK = 8             # rétrécissement de la note d'un adversaire dans sa propre ligue
MAX_GOALS = 10


# --------------------------------------------------------------------------- parseurs
def parse_match_stats(html: str) -> dict | None:
    """Statistiques d'une feuille de match : {"home": {...}, "away": {...}} avec shots, off_target, saved,
    corners, free_kicks, fouls, offsides ; None si la page ne publie pas de statistiques."""
    labels = re.findall(r'class="unterueberschrift[^"]*">\s*([^<]+?)\s*<', html)
    values = [int(v) for v in re.findall(r'sb-statistik-zahl[^>]*>\s*(\d+)', html)]
    keys = {"total shots": "shots", "shots off target": "off_target", "shots saved": "saved", "corners": "corners",
            "free kicks": "free_kicks", "fouls": "fouls", "offsides": "offsides", "possession": "possession"}
    stat_labels = [keys[l.lower()] for l in labels if l.lower() in keys]
    if "possession" in stat_labels and len(values) == 2 * (len(stat_labels) - 1):
        stat_labels.remove("possession")          # possession non publiée : pas de valeurs
    if not stat_labels or len(values) < 2 * len(stat_labels):
        return None
    out = {"home": {}, "away": {}}
    for i, key in enumerate(stat_labels):
        out["home"][key], out["away"][key] = values[2 * i], values[2 * i + 1]
    if "shots" not in out["home"]:
        return None
    return out


def main_league(fixtures: list[dict]) -> str | None:
    """Championnat d'un club : compétition la plus fréquente de son calendrier hors coupes."""
    codes = Counter(m["competition_id"] for m in fixtures if m.get("competition_id")
                    and competition_type(m["competition_id"], m.get("competition")) in ("league", "other")
                    and "cup" not in (m.get("competition") or "").lower())
    return codes.most_common(1)[0][0] if codes else None


# --------------------------------------------------------------------------- statistiques
def _solve(A: list[list[float]], b: list[float]) -> list[float]:
    """Résout A x = b (Gauss avec pivot)."""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c and M[c][c]:
                f = M[r][c] / M[c][c]
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] if M[i][i] else 0.0 for i in range(n)]


def fit_xg(rows: list[dict]) -> dict:
    """buts ≈ a × tirs cadrés + b × tirs non cadrés, moindres carrés sans constante.
    rows : [{"goals", "on_target", "off_target"}]."""
    A = [[sum(r["on_target"] ** 2 for r in rows), sum(r["on_target"] * r["off_target"] for r in rows)],
         [sum(r["on_target"] * r["off_target"] for r in rows), sum(r["off_target"] ** 2 for r in rows)]]
    b = [sum(r["goals"] * r["on_target"] for r in rows), sum(r["goals"] * r["off_target"] for r in rows)]
    a, c = _solve(A, b)
    a, c = max(a, 0.0), max(c, 0.0)
    pred = [a * r["on_target"] + c * r["off_target"] for r in rows]
    goals = [r["goals"] for r in rows]
    mean = sum(goals) / len(goals)
    ss_res = sum((g - p) ** 2 for g, p in zip(goals, pred))
    ss_tot = sum((g - mean) ** 2 for g in goals) or 1.0
    return {"par_tir_cadre": round(a, 4), "par_tir_non_cadre": round(c, 4), "n": len(rows),
            "r2": round(1 - ss_res / ss_tot, 3), "buts": sum(goals), "xg": round(sum(pred), 1)}


def xg_of(stat: dict, goals: int, model: dict) -> float:
    on = stat.get("saved", 0) + goals
    return model["par_tir_cadre"] * on + model["par_tir_non_cadre"] * stat.get("off_target", 0)


def fit_ratings(matches: list[dict], teams: list[str], prior: dict[str, tuple[float, float]] | None = None,
                prior_matches: float = PRIOR_MATCHES, iterations: int = 200) -> dict:
    """Modèle de Poisson multiplicatif (Maher) : λ_dom = exp(μ + h + att_d − def_e), λ_ext = exp(μ + att_e − def_d).

    matches : [{"home", "away", "y_home", "y_away"}] (y = cible : buts, xG ou mélange).
    prior : {club: (att0, def0)} ; chaque club reçoit prior_matches pseudo-matchs à sa note a priori.
    Retourne {"mu", "home", "att": {}, "def": {}}.
    """
    prior = prior or {}
    att = {t: prior.get(t, (0.0, 0.0))[0] for t in teams}
    dfn = {t: prior.get(t, (0.0, 0.0))[1] for t in teams}
    total = sum(m["y_home"] + m["y_away"] for m in matches)
    mu = math.log(max(total / (2 * max(1, len(matches))), 0.1))
    h = 0.0
    for _ in range(iterations):
        hs = sum(m["y_home"] for m in matches)
        as_ = sum(m["y_away"] for m in matches)
        # μ fixé par les buts à l'extérieur, h par les buts à domicile
        mu = math.log(max(as_, 1e-6) / sum(math.exp(att[m["away"]] - dfn[m["home"]]) for m in matches))
        h = math.log(max(hs, 1e-6) / sum(math.exp(mu + att[m["home"]] - dfn[m["away"]]) for m in matches))
        h = max(-0.5, min(0.6, h))
        for t in teams:
            p_att, p_def = prior.get(t, (0.0, 0.0))
            scored = sum(m["y_home"] for m in matches if m["home"] == t) + sum(m["y_away"] for m in matches if m["away"] == t)
            base = sum(math.exp(mu + h - dfn[m["away"]]) for m in matches if m["home"] == t) + \
                sum(math.exp(mu - dfn[m["home"]]) for m in matches if m["away"] == t)
            # pseudo-matchs : prior_matches matchs « moyens » marqués au rythme a priori
            avg = math.exp(mu + h / 2)
            att[t] = math.log((scored + prior_matches * avg * math.exp(p_att)) / (base + prior_matches * avg))
            conceded = sum(m["y_away"] for m in matches if m["home"] == t) + sum(m["y_home"] for m in matches if m["away"] == t)
            base_c = sum(math.exp(mu + att[m["away"]]) for m in matches if m["home"] == t) + \
                sum(math.exp(mu + h + att[m["home"]]) for m in matches if m["away"] == t)
            dfn[t] = -math.log((conceded + prior_matches * avg * math.exp(-p_def)) / (base_c + prior_matches * avg))
        ma, md = sum(att.values()) / len(att), sum(dfn.values()) / len(dfn)
        att = {t: v - ma for t, v in att.items()}
        dfn = {t: v - md for t, v in dfn.items()}
    return {"mu": mu, "home": h, "att": att, "def": dfn}


def poisson_pmf(lam: float, k: int) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def score_matrix(lh: float, la: float, n: int = MAX_GOALS) -> list[list[float]]:
    return [[poisson_pmf(lh, i) * poisson_pmf(la, j) for j in range(n + 1)] for i in range(n + 1)]


def outcome_probs(lh: float, la: float) -> dict:
    M = score_matrix(lh, la)
    n = len(M)
    win = sum(M[i][j] for i in range(n) for j in range(n) if i > j)
    draw = sum(M[i][i] for i in range(n))
    loss = sum(M[i][j] for i in range(n) for j in range(n) if i < j)
    scores = sorted(((M[i][j], f"{i}:{j}") for i in range(n) for j in range(n)), reverse=True)[:6]
    over = sum(M[i][j] for i in range(n) for j in range(n) if i + j >= 3)
    btts = sum(M[i][j] for i in range(1, n) for j in range(1, n))
    return {"dom": round(win, 4), "nul": round(draw, 4), "ext": round(loss, 4),
            "xg_dom": round(lh, 2), "xg_ext": round(la, 2),
            "clean_sheet_dom": round(math.exp(-la), 4), "clean_sheet_ext": round(math.exp(-lh), 4),
            "plus_2_5": round(over, 4), "les_deux_marquent": round(btts, 4),
            "scores": [{"score": s, "p": round(p, 4)} for p, s in scores]}


def fit_cup_model(rows: list[dict], iterations: int = 60) -> dict:
    """Régression de Poisson log λ = α + β × ln(coef_équipe / coef_adverse) + h × domicile (IRLS, 3 paramètres).

    rows : [{"goals", "gap" (ln du rapport de coefficients), "home" (0/1)}]."""
    beta = [math.log(max(sum(r["goals"] for r in rows) / len(rows), 0.1)), 0.5, 0.0]
    X = [[1.0, r["gap"], float(r["home"])] for r in rows]
    y = [r["goals"] for r in rows]
    for _ in range(iterations):
        lam = [math.exp(sum(b * x for b, x in zip(beta, xi))) for xi in X]
        grad = [sum((yi - li) * xi[k] for xi, yi, li in zip(X, y, lam)) for k in range(3)]
        H = [[sum(li * xi[k] * xi[m] for xi, li in zip(X, lam)) + (1e-3 if k == m else 0) for m in range(3)] for k in range(3)]
        step = _solve(H, grad)
        beta = [b + s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < 1e-8:
            break
    return {"alpha": beta[0], "beta": beta[1], "home": beta[2], "n": len(rows)}


def knockout(lh: float, la: float) -> dict:
    """Coupe : 90 minutes, puis prolongation (λ × 1/3) en cas d'égalité, puis tirs au but à 50 %."""
    reg = outcome_probs(lh, la)
    et = outcome_probs(lh / 3, la / 3)
    p_home = reg["dom"] + reg["nul"] * (et["dom"] + 0.5 * et["nul"])
    return {**reg, "qualif_dom": round(p_home, 4), "qualif_ext": round(1 - p_home, 4),
            "prolongation": round(reg["nul"], 4), "tirs_au_but": round(reg["nul"] * et["nul"], 4)}


def simulate_season(ratings: dict, played: list[dict], remaining: list[dict], teams: list[str],
                    n: int = 20000, seed: int = 26) -> dict:
    """Monte-Carlo de la fin de saison : points actuels + matchs restants tirés au Poisson."""
    rng = random.Random(seed)
    pts0, gd0 = Counter(), Counter()
    for m in played:
        gh, ga = m["score"]
        pts0[m["home"]] += 3 if gh > ga else 1 if gh == ga else 0
        pts0[m["away"]] += 3 if ga > gh else 1 if gh == ga else 0
        gd0[m["home"]] += gh - ga
        gd0[m["away"]] += ga - gh
    lams = []
    for m in remaining:
        lh = math.exp(ratings["mu"] + ratings["home"] + ratings["att"][m["home"]] - ratings["def"][m["away"]])
        la = math.exp(ratings["mu"] + ratings["att"][m["away"]] - ratings["def"][m["home"]])
        lams.append((m["home"], m["away"], lh, la))

    def pois(lam):
        L, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= rng.random()
            if p <= L:
                return k
            k += 1

    ranks = {t: Counter() for t in teams}
    pts_sum = Counter()
    for _ in range(n):
        pts, gd = Counter(pts0), Counter(gd0)
        for h, a, lh, la in lams:
            gh, ga = pois(lh), pois(la)
            pts[h] += 3 if gh > ga else 1 if gh == ga else 0
            pts[a] += 3 if ga > gh else 1 if gh == ga else 0
            gd[h] += gh - ga
            gd[a] += ga - gh
        order = sorted(teams, key=lambda t: (pts[t], gd[t], rng.random()), reverse=True)
        for i, t in enumerate(order):
            ranks[t][i + 1] += 1
            pts_sum[t] += pts[t]
    out = {}
    for t in teams:
        out[t] = {"points_actuels": pts0[t], "points_projetes": round(pts_sum[t] / n, 1),
                  "rang_moyen": round(sum(r * c for r, c in ranks[t].items()) / n, 2),
                  "p_rang": {r: round(ranks[t][r] / n, 4) for r in range(1, len(teams) + 1)}}
    return out


# --------------------------------------------------------------------------- onze
SLOTS_433 = [("GK", "Gardien"), ("RB", "Latéral droit"), ("CB1", "Défenseur central"), ("CB2", "Défenseur central"),
             ("LB", "Latéral gauche"), ("DM", "Milieu défensif"), ("CM1", "Milieu central"), ("CM2", "Milieu offensif"),
             ("RW", "Ailier droit"), ("CF", "Avant-centre"), ("LW", "Ailier gauche")]
# poste Transfermarkt → (créneau principal, créneaux secondaires)
POS_SLOTS = {
    "goalkeeper": ("GK", ()), "centre-back": ("CB", ("DM",)), "left-back": ("LB", ("CB", "LW")),
    "right-back": ("RB", ("CB", "RW")), "defensive midfield": ("DM", ("CM", "CB")),
    "central midfield": ("CM", ("DM",)), "attacking midfield": ("CM", ("RW", "LW", "CF")),
    "left midfield": ("LW", ("CM", "LB")), "right midfield": ("RW", ("CM", "RB")),
    "left winger": ("LW", ("RW", "CF")), "right winger": ("RW", ("LW", "CF")),
    "centre-forward": ("CF", ("RW", "LW")), "second striker": ("CF", ("CM", "RW", "LW")),
}


def slot_fit(positions: Counter, squad_pos: str | None, slot: str) -> float:
    """Adéquation 0-1 d'un joueur à un créneau : postes occupés cette saison (pondérés), sinon poste déclaré."""
    base = slot.rstrip("12")
    total = sum(positions.values())
    if total:
        fit = 0.0
        for pos, n in positions.items():
            main, sec = POS_SLOTS.get((pos or "").lower(), (None, ()))
            if main == base:
                fit += n
            elif base in sec:
                fit += 0.75 * n
        if fit:
            return min(1.0, fit / total)
    main, sec = POS_SLOTS.get((squad_pos or "").lower(), (None, ()))
    return 1.0 if main == base else 0.75 if base in sec else 0.0


def pick_xi(players: list[dict], slots=SLOTS_433) -> list[dict]:
    """Affectation joueurs → créneaux maximisant Σ note × adéquation (glouton puis échanges 2 à 2)."""
    cand = [(p["selection"] * slot_fit(p["positions"], p["position"], s), p["id"], s) for p in players for s, _ in slots]
    cand = [c for c in cand if c[0] > 0]
    cand.sort(reverse=True)
    used_p, used_s, assign = set(), set(), {}
    for v, pid, s in cand:
        if pid in used_p or s in used_s:
            continue
        assign[s] = pid
        used_p.add(pid)
        used_s.add(s)
    by_id = {p["id"]: p for p in players}

    def val(s, pid):
        return by_id[pid]["selection"] * slot_fit(by_id[pid]["positions"], by_id[pid]["position"], s)

    improved = True
    while improved:
        improved = False
        keys = list(assign)
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                if val(a, assign[b]) + val(b, assign[a]) > val(a, assign[a]) + val(b, assign[b]) + 1e-9:
                    assign[a], assign[b] = assign[b], assign[a]
                    improved = True
        for s in keys:   # remplaçant hors onze meilleur à ce créneau
            for p in players:
                if p["id"] not in assign.values() and val(s, p["id"]) > val(s, assign[s]) + 1e-9:
                    assign[s] = p["id"]
                    improved = True
    return [{"creneau": s, "role": label, **{k: by_id[assign[s]][k] for k in ("id", "name", "position", "selection",
                                                                               "sport", "part_3_derniers", "dynamique")},
             "adequation": round(slot_fit(by_id[assign[s]]["positions"], by_id[assign[s]]["position"], s), 2)}
            for s, label in slots if s in assign]


# --------------------------------------------------------------------------- collecte et assemblage
def collect(client: MatchlogClient, season: int, today: str) -> dict:
    ml = json.loads((DATA / "matchlog_2627.json").read_text())
    c2_clubs = sorted(cid for cid, cl in ml["clubs"].items()
                      if any(ml["matches"].get(mid, {}).get("competition_id") == "C2" for mid in cl["matches"]))
    fixtures = {cid: client.fixtures(cid, season) for cid in c2_clubs}
    league_matches, seen = [], set()
    for cid, fx in fixtures.items():
        for m in fx:
            if m["competition_id"] == "C2" and m["id"] not in seen:
                seen.add(m["id"])
                league_matches.append(m)
    played = [m for m in league_matches if m["played"] and (m["date"] or "") <= today]
    remaining = [m for m in league_matches if not m["played"] or (m["date"] or "") > today]
    stats = {}
    for m in played:
        stats[m["id"]] = parse_match_stats(client.fetch(f"/spielbericht/statistik/spielbericht/{m['id']}"))
    # Coupe de Suisse 1er tour : clubs pros (vivier) contre amateurs
    cup = [dict(mid=mid, **m) for mid, m in ml["matches"].items() if m["competition_id"] == "SCC"
           and str(m["matchday"]).lower().startswith("first")]
    pros = {r["club_id"] for b in json.loads((DATA / "defence.json").read_text()).values()
            if b["competition_id"] in ("C1", "C2") and int(b["season_id"]) == season for r in b["table"]}
    amateur_league = {}
    for m in cup:
        for side in ("home_id", "away_id"):
            cid = m[side]
            if cid not in pros and cid not in amateur_league:
                try:
                    amateur_league[cid] = main_league(client.fixtures(cid, season))
                except Exception as e:  # un club amateur sans calendrier lisible est ignoré
                    print(f"  ! calendrier {cid} : {e}")
                    amateur_league[cid] = None
    return {"ml": ml, "c2_clubs": c2_clubs, "fixtures": fixtures, "played": played, "remaining": remaining,
            "stats": stats, "cup": cup, "pros": pros, "amateur_league": amateur_league}


def club_names(raw: dict) -> dict:
    names = {}
    for fx in raw["fixtures"].values():
        for m in fx:
            names[m["home_id"]] = m["home"]
            names[m["away_id"]] = m["away"]
    return names


def team_ratings(raw: dict, defence: dict, season: int) -> tuple[dict, dict, dict]:
    """xG-tirs, notes Poisson (buts, xG-tirs, mélange) ; renvoie (xg_model, ratings, per_match)."""
    rows, per_match = [], {}
    for m in raw["played"]:
        st = raw["stats"].get(m["id"])
        gh, ga = (int(v) for v in m["result"].split(":"))
        if st:
            rows.append({"goals": gh, "on_target": st["home"].get("saved", 0) + gh, "off_target": st["home"].get("off_target", 0)})
            rows.append({"goals": ga, "on_target": st["away"].get("saved", 0) + ga, "off_target": st["away"].get("off_target", 0)})
        per_match[m["id"]] = {"home": m["home_id"], "away": m["away_id"], "score": (gh, ga), "stats": st, "date": m["date"],
                              "matchday": m["matchday"]}
    xg_model = fit_xg(rows)
    for pm in per_match.values():
        st = pm["stats"]
        pm["xg"] = (round(xg_of(st["home"], pm["score"][0], xg_model), 2), round(xg_of(st["away"], pm["score"][1], xg_model), 2)) if st else None
    # a priori : classement 2025/26 de Challenge League
    prior = {}
    prev = (defence.get(f"C2/{season - 1}") or {}).get("table") or []
    if prev:
        gf = sum(t["goals_for"] for t in prev) / sum(t["matches"] for t in prev)
        for t in prev:
            if t["club_id"] in raw["c2_clubs"] and t["matches"]:
                prior[t["club_id"]] = (math.log(max(t["goals_for"] / t["matches"], 0.2) / gf),
                                       -math.log(max(t["goals_against"] / t["matches"], 0.2) / gf))
    teams = raw["c2_clubs"]

    def fit(kind):
        ms = []
        for pm in per_match.values():
            g = pm["score"]
            x = pm["xg"] or g
            y = {"buts": g, "xg": x, "mix": (0.5 * g[0] + 0.5 * x[0], 0.5 * g[1] + 0.5 * x[1])}[kind]
            ms.append({"home": pm["home"], "away": pm["away"], "y_home": y[0], "y_away": y[1]})
        return fit_ratings(ms, teams, prior)

    ratings = {k: fit(k) for k in ("buts", "xg", "mix")}
    ratings["prior"] = prior
    return xg_model, ratings, per_match


def table_now(per_match: dict, teams: list[str], names: dict, ratings: dict) -> list[dict]:
    rows = {t: {"club_id": t, "club": names.get(t, t), "j": 0, "pts": 0, "bp": 0, "bc": 0, "xg_pour": 0.0, "xg_contre": 0.0}
            for t in teams}
    for pm in per_match.values():
        (gh, ga), h, a = pm["score"], pm["home"], pm["away"]
        for t, gf, gc, xf, xc in ((h, gh, ga, *(pm["xg"] or (gh, ga))), (a, ga, gh, *(reversed(pm["xg"] or (gh, ga))))):
            r = rows[t]
            r["j"] += 1
            r["bp"] += gf
            r["bc"] += gc
            r["xg_pour"] += xf
            r["xg_contre"] += xc
            r["pts"] += 3 if gf > gc else 1 if gf == gc else 0
    mix = ratings["mix"]
    out = sorted(rows.values(), key=lambda r: (r["pts"], r["bp"] - r["bc"], r["bp"]), reverse=True)
    for i, r in enumerate(out):
        r["rang"] = i + 1
        r["xg_pour"], r["xg_contre"] = round(r["xg_pour"], 1), round(r["xg_contre"], 1)
        r["attaque"], r["defense"] = round(math.exp(mix["att"][r["club_id"]]), 2), round(math.exp(mix["def"][r["club_id"]]), 2)
    return out


def form(per_match: dict, club: str, n: int = 5) -> list[dict]:
    ms = sorted((pm for pm in per_match.values() if club in (pm["home"], pm["away"])), key=lambda pm: pm["date"])[-n:]
    out = []
    for pm in ms:
        home = pm["home"] == club
        gf, ga = pm["score"] if home else tuple(reversed(pm["score"]))
        xf, xa = (pm["xg"] if home else tuple(reversed(pm["xg"]))) if pm["xg"] else (None, None)
        out.append({"date": pm["date"], "journee": pm["matchday"], "dom": home, "adversaire": pm["away"] if home else pm["home"],
                    "score": f"{gf}:{ga}", "issue": "V" if gf > ga else "N" if gf == ga else "D", "xg_pour": xf, "xg_contre": xa})
    return out


def squad_selection(squad: list[dict], cand: list[dict], ml: dict, today: date, standings: dict) -> list[dict]:
    """Note de sélection de chaque joueur d'Yverdon (Sport, temps de jeu récent, dynamique), disponibilité."""
    baselines = pool_baselines(cand)
    logs = ml["players"]
    out = []
    for p in squad:
        dyn = season_dynamics(p, ml, today, standings)
        s, det = sport_score(p, baselines, dyn, today)
        entries = [e for e in (logs.get(p["id"]) or {}).get("entries", []) if e["status"] != "other"]
        last3 = entries[-3:]
        poss = sum(max(90, e["minutes"]) for e in last3) or 1
        part3 = sum(e["minutes"] for e in last3) / poss
        positions = Counter(e["position"] for e in entries if e["status"] == "played" and e.get("position"))
        cur = (logs.get(p["id"]) or {}).get("current_status")
        available = not (cur and cur.get("status") in ("injured", "suspended"))
        out.append({"id": p["id"], "name": p["name"], "position": p.get("position"), "groupe": position_group(p.get("position")),
                    "age": p.get("age"), "sport": round(s, 1), "part_3_derniers": round(part3, 2),
                    "dynamique": round((dyn or {}).get("score", 0.5), 3),
                    "selection": round(0.45 * s / 100 + 0.35 * part3 + 0.20 * (dyn or {}).get("score", 0.5), 4),
                    "positions": positions, "disponible": available, "statut": cur,
                    "minutes_2627": (dyn or {}).get("minutes", 0), "buts_2627": (dyn or {}).get("buts", 0),
                    "passes_2627": (dyn or {}).get("passes", 0), "titularisations": (dyn or {}).get("titularisations", 0),
                    "jaunes_2627": (dyn or {}).get("jaunes", 0), "detail_sport": det})
    return out


def scorer_probs(xi: list[dict], bench: list[dict], players: list[dict], lam: float, cand_stats: dict) -> list[dict]:
    """P(marquer) = 1 − exp(−λ_équipe × part du joueur), part = taux de buts/90 (26/27 + 25/26, rétréci vers le poste)
    × minutes attendues (onze 80 min, trois premiers remplaçants offensifs 20 min), normalisée sur l'équipe."""
    prior90 = {"GK": 0.0, "DEF": 0.05, "MID": 0.12, "ATT": 0.30}
    by_id = {p["id"]: p for p in players}
    mins = {r["id"]: 80 for r in xi}
    for r in bench[:3]:
        mins[r["id"]] = 20
    rates = {}
    for pid, m in mins.items():
        p = by_id[pid]
        g, minutes = cand_stats.get(pid, (0, 0))
        k = 900
        rates[pid] = (g + prior90[p["groupe"]] * k / 90) / (minutes + k) * 90
    weights = {pid: rates[pid] * mins[pid] / 90 for pid in mins}
    total = sum(weights.values()) or 1
    out = []
    for pid, w in weights.items():
        share = w / total
        out.append({"id": pid, "name": by_id[pid]["name"], "minutes_attendues": mins[pid], "buts_90": round(rates[pid], 2),
                    "xg_match": round(lam * share, 2), "p_marquer": round(1 - math.exp(-lam * share), 3)})
    out.sort(key=lambda r: -r["p_marquer"])
    return out


def player_goal_history(p: dict) -> tuple[int, int]:
    """Buts et minutes de championnat 26/27 + 25/26 (lignes Transfermarkt), toutes compétitions pour 26/27."""
    g = m = 0
    for r in p.get("stats", []):
        if r.get("season") in ("26/27", "25/26") and r.get("minutes"):
            if r["season"] == "25/26" and league_coef(r.get("competition_id")) is None:
                continue
            g += r.get("goals", 0)
            m += r["minutes"]
    return g, m


def build(today: date, season: int = 2026, client: MatchlogClient | None = None) -> dict:
    client = client or MatchlogClient()
    t = today.isoformat()
    raw = collect(client, season, t)
    defence = json.loads((DATA / "defence.json").read_text())
    names = club_names(raw)
    xg_model, ratings, per_match = team_ratings(raw, defence, season)
    mix = ratings["mix"]
    teams = raw["c2_clubs"]
    standings = standings_from_defence(defence, season)
    table = table_now(per_match, teams, names, ratings)

    # --- prochains matchs de championnat (N_LEAGUE premiers du calendrier)
    ys_next = sorted((m for m in raw["remaining"] if YS in (m["home_id"], m["away_id"])), key=lambda m: m["date"])

    def predict_league(fx):
        out = {}
        for k in ("mix", "buts", "xg"):
            r = ratings[k]
            a = math.exp(r["mu"] + r["home"] + r["att"][fx["home_id"]] - r["def"][fx["away_id"]])
            b = math.exp(r["mu"] + r["att"][fx["away_id"]] - r["def"][fx["home_id"]])
            out[k] = outcome_probs(a, b)
        return out["mix"], {k: {kk: v for kk, v in out[k].items() if kk in ("dom", "nul", "ext", "xg_dom", "xg_ext")}
                            for k in ("buts", "xg")}

    lg = ys_next[0]
    league_pred, league_variants = predict_league(lg)
    lh, la = league_pred["xg_dom"], league_pred["xg_ext"]

    # --- coupe : modèle pro-amateur sur le 1er tour 2026/27
    def coef_of(cid):
        if cid in raw["pros"]:
            code = next((b["competition_id"] for b in defence.values() if int(b["season_id"]) == season
                         and b["competition_id"] in ("C1", "C2") and any(r["club_id"] == cid for r in b["table"])), None)
            return league_coef(code), code
        code = raw["amateur_league"].get(cid)
        return (league_coef(code) if code else None), code

    cup_rows, cup_used = [], []
    for m in raw["cup"]:
        ch, code_h = coef_of(m["home_id"])
        ca, code_a = coef_of(m["away_id"])
        if not ch or not ca or not m.get("score"):
            continue
        gh, ga = m["score"]
        cup_rows.append({"goals": gh, "gap": math.log(ch / ca), "home": 1})
        cup_rows.append({"goals": ga, "gap": math.log(ca / ch), "home": 0})
        cup_used.append({"date": m["date"], "dom": m["home"], "dom_ligue": code_h, "ext": m["away"], "ext_ligue": code_a,
                         "score": f"{gh}:{ga}"})
    cup_model = fit_cup_model(cup_rows)
    cup_fx = next(m for m in raw["fixtures"][YS] if not m["played"] and m["competition_id"] not in ("C2",)
                  and m["date"] < lg["date"]) if any(not m["played"] and m["competition_id"] != "C2" and m["date"] < lg["date"]
                                                    for m in raw["fixtures"][YS]) else None
    cup = None
    if cup_fx:
        opp = cup_fx["home_id"] if cup_fx["away_id"] == YS else cup_fx["away_id"]
        opp_fx = client.fixtures(opp, season)
        opp_code = main_league(opp_fx)
        opp_table = client.league_table(opp_code, season) if opp_code else []
        row = next((r for r in opp_table if r["club_id"] == opp), None)
        att_o = def_o = 0.0
        if row and opp_table:
            avg = sum(r["goals_for"] for r in opp_table) / max(1, sum(r["matches"] for r in opp_table))
            att_o = math.log((row["goals_for"] + OPP_SHRINK * avg) / ((row["matches"] + OPP_SHRINK) * avg))
            def_o = -math.log((row["goals_against"] + OPP_SHRINK * avg) / ((row["matches"] + OPP_SHRINK) * avg))
        c_ys, c_opp = 1.0, league_coef(opp_code) or 0.3
        ys_home = cup_fx["home_id"] == YS
        lam_ys = math.exp(cup_model["alpha"] + cup_model["beta"] * math.log(c_ys / c_opp) + cup_model["home"] * ys_home
                          + mix["att"][YS] - def_o)
        lam_opp = math.exp(cup_model["alpha"] + cup_model["beta"] * math.log(c_opp / c_ys) + cup_model["home"] * (not ys_home)
                           + att_o - mix["def"][YS])
        lh_c, la_c = (lam_ys, lam_opp) if ys_home else (lam_opp, lam_ys)
        ko = knockout(lh_c, la_c)
        cup = {"match": {k: cup_fx[k] for k in ("id", "date", "competition_id", "competition", "matchday", "home_id", "home",
                                                 "away_id", "away")},
               "adversaire": {"id": opp, "ligue": opp_code, "coefficient": c_opp, "classement": row,
                              "equipes": len(opp_table), "forme": [{"date": m["date"], "competition": m["competition"],
                                                                    "dom": m["home"], "ext": m["away"], "score": m["result"]}
                                                                   for m in opp_fx if m["played"]][-6:],
                              "attaque_relative": round(math.exp(att_o), 2), "defense_relative": round(math.exp(def_o), 2)},
               "modele": {"alpha": round(cup_model["alpha"], 3), "beta": round(cup_model["beta"], 3),
                          "domicile": round(cup_model["home"], 3), "matchs": len(cup_used), "base": cup_used},
               "prediction": ko}

    # --- effectif et onze
    cand = json.loads((DATA / "candidates.json").read_text())["players"]
    squad = json.loads((DATA / "squad.json").read_text())["players"]
    players = squad_selection(squad, cand, raw["ml"], today, standings)
    available = [p for p in players if p["disponible"]]
    xi = pick_xi(available)
    in_xi = {r["id"] for r in xi}
    bench = sorted((p for p in available if p["id"] not in in_xi), key=lambda p: -p["selection"])[:9]
    bench_rows = [{k: p[k] for k in ("id", "name", "position", "selection", "sport", "part_3_derniers")} for p in bench]
    goal_hist = {p["id"]: player_goal_history(p) for p in squad}

    # Coupe : le gardien du 1er tour (Rouiller) et rotation des joueurs les plus chargés si l'écart de note est faible
    cup_xi = None
    if cup:
        cup_pool = list(available)
        cup_keeper = None
        prev_cup = [mid for mid, m in raw["ml"]["matches"].items() if m["competition_id"] == "SCC" and YS in (m["home_id"], m["away_id"])]
        for mid in prev_cup:
            for p in players:
                e = next((e for e in raw["ml"]["players"].get(p["id"], {}).get("entries", []) if e["match_id"] == mid), None)
                if e and e["status"] == "played" and p["groupe"] == "GK":
                    cup_keeper = p["id"]
        rested = []
        load = sorted((r for r in xi if r["creneau"] != "GK"), key=lambda r: -next(p["minutes_2627"] for p in players if p["id"] == r["id"]))
        for r in load[:3]:
            p = next(p for p in players if p["id"] == r["id"])
            alt = max((q for q in available if q["id"] not in in_xi and slot_fit(q["positions"], q["position"], r["creneau"]) >= 0.75),
                      key=lambda q: q["selection"], default=None)
            if alt and p["minutes_2627"] >= 750 and alt["selection"] >= 0.8 * p["selection"]:
                rested.append(p["id"])
        if cup_keeper:
            rested.append(next(r["id"] for r in xi if r["creneau"] == "GK"))
        cup_pool = [p for p in available if p["id"] not in rested]
        cup_xi = pick_xi([{**p, "selection": p["selection"] + (1.0 if p["id"] == cup_keeper else 0)} for p in cup_pool])
        for r in cup_xi:
            r["selection"] = next(p["selection"] for p in players if p["id"] == r["id"])
        cup["onze"] = cup_xi
        cup["repos"] = [{"id": pid, "name": next(p["name"] for p in players if p["id"] == pid)} for pid in rested]
        cup_lam_ys = cup["prediction"]["xg_ext"] if cup["match"]["away_id"] == YS else cup["prediction"]["xg_dom"]
        cup_in = {r["id"] for r in cup_xi}
        cup_bench = [{k: p[k] for k in ("id", "name", "position", "selection", "sport", "part_3_derniers")}
                     for p in sorted((p for p in available if p["id"] not in cup_in), key=lambda p: -p["selection"])[:9]]
        cup["banc"] = cup_bench
        cup["buteurs"] = scorer_probs(cup_xi, cup_bench, players, cup_lam_ys, goal_hist)

    # --- dossier adversaire
    def dossier(opp):
        opp_fx = raw["fixtures"].get(opp) or client.fixtures(opp, season)
        systems = Counter(m["formation"] for m in opp_fx if m["played"] and m.get("formation"))
        coach = next((m["coach"] for m in reversed(opp_fx) if m["played"] and m.get("coach")), None)
        opp_players = []
        for pid, log in raw["ml"]["players"].items():
            if log["club_id"] != opp:
                continue
            e = [x for x in log["entries"] if x["status"] == "played"]
            if e:
                opp_players.append({"id": pid, "name": log["name"], "matchs": len(e), "minutes": sum(x["minutes"] for x in e),
                                    "buts": sum(x["goals"] for x in e), "passes": sum(x["assists"] for x in e),
                                    "titularisations": sum(1 for x in e if x["started"]),
                                    "poste": Counter(x["position"] for x in e if x.get("position")).most_common(1)[0][0]
                                    if any(x.get("position") for x in e) else None,
                                    "encaisses": sum(x.get("conceded") or 0 for x in e),
                                    "statut": (log.get("current_status") or {}).get("status")})
        opp_players.sort(key=lambda r: (-(r["buts"] + 0.7 * r["passes"]), -r["minutes"]))
        keepers = [r for r in opp_players if (r["poste"] or "").lower() == "goalkeeper"]
        injured = [{"id": pid, "name": log["name"], **(log.get("current_status") or {})}
                   for pid, log in raw["ml"]["players"].items() if log["club_id"] == opp
                   and (log.get("current_status") or {}).get("status") in ("injured", "suspended")]
        h2h = [{"date": pm["date"], "journee": pm["matchday"], "home": pm["home"], "away": pm["away"],
                "score": f"{pm['score'][0]}:{pm['score'][1]}", "xg": pm["xg"]}
               for pm in sorted(per_match.values(), key=lambda pm: pm["date"]) if {pm["home"], pm["away"]} == {YS, opp}]
        row = next(r for r in table if r["club_id"] == opp)
        return {"id": opp, "systemes": dict(systems), "entraineur": coach, "joueurs_du_vivier": opp_players[:8],
                "gardiens": keepers[:2], "indisponibles": injured, "confrontations": h2h, "classement": row,
                "attaque": round(math.exp(mix["att"][opp]), 2), "defense": round(math.exp(mix["def"][opp]), 2)}

    ys_fx = raw["fixtures"][YS]
    ys_systems = Counter(m["formation"] for m in ys_fx if m["played"] and m.get("formation"))
    ys_coach = next((m["coach"] for m in reversed(ys_fx) if m["played"] and m.get("coach")), None)
    coach_systems = Counter(m["formation"] for m in ys_fx if m["played"] and m.get("coach") == ys_coach)

    # --- dernier onze aligné par l'entraîneur en place, et écarts avec la recommandation
    last_id = max((m for m in ys_fx if m["played"] and m["date"] <= t), key=lambda m: m["date"])["id"]
    last_xi = [{"id": pid, "name": log["name"], "position": e["position"]}
               for pid, log in raw["ml"]["players"].items() if log["club_id"] == YS
               for e in log["entries"] if e["match_id"] == last_id and e["started"]]
    last_ids = {r["id"] for r in last_xi}
    by_id = {p["id"]: p for p in players}

    def why(pid):
        p = by_id[pid]
        return {"disponible": p["disponible"], "statut": (p["statut"] or {}).get("status"), "selection": p["selection"],
                "sport": p["sport"], "part_3_derniers": p["part_3_derniers"], "buts": p["buts_2627"], "passes": p["passes_2627"]}

    ecarts = {"dernier_match": last_id,
              "entrants": [{"id": r["id"], "name": r["name"], "raison": why(r["id"])} for r in xi if r["id"] not in last_ids],
              "sortants": [{"id": r["id"], "name": r["name"], "raison": why(r["id"])} for r in last_xi if r["id"] not in in_xi]}

    def league_block(fx):
        pred, variants = predict_league(fx)
        opp = fx["away_id"] if fx["home_id"] == YS else fx["home_id"]
        lam = pred["xg_dom"] if fx["home_id"] == YS else pred["xg_ext"]
        return {"match": {k: fx[k] for k in ("id", "date", "competition_id", "matchday", "home_id", "home", "away_id", "away")},
                "prediction": pred, "variantes": variants, "forme_ys": form(per_match, YS),
                "forme_adversaire": form(per_match, opp), "adversaire": dossier(opp),
                "onze": xi, "banc": bench_rows, "buteurs": scorer_probs(xi, bench_rows, players, lam, goal_hist),
                "dernier_onze": last_xi, "ecarts": ecarts}

    league_blocks = [league_block(fx) for fx in ys_next[:2]]

    # --- saison
    played_c2 = [{"home": pm["home"], "away": pm["away"], "score": pm["score"]} for pm in per_match.values()]
    remaining = [{"home": m["home_id"], "away": m["away_id"]} for m in raw["remaining"]]
    sim = simulate_season(mix, played_c2, remaining, teams)

    return {
        "genere": date.today().isoformat(), "today": t, "saison": f"{season % 100:02d}/{(season + 1) % 100:02d}",
        "source": "www.transfermarkt.com : résultats, statistiques de tirs des feuilles de match, compositions, "
                  "classements, journal match par match 2026/27",
        "xg_modele": xg_model,
        "notes_equipes": {"mu": round(mix["mu"], 3), "avantage_terrain": round(math.exp(mix["home"]), 3),
                          "prior_matchs": PRIOR_MATCHES, "clubs_avec_prior": sorted(ratings["prior"])},
        "classement": table,
        "noms": names,
        "prochain_match": {"championnat": league_blocks[0], "championnat_matchs": league_blocks,
                           "coupe": cup},
        "yverdon": {"entraineur": ys_coach, "systemes_saison": dict(ys_systems), "systemes_entraineur": dict(coach_systems),
                    "systeme_retenu": "4-3-3",
                    "indisponibles": [{"id": p["id"], "name": p["name"], "statut": p["statut"]} for p in players if not p["disponible"]],
                    "joueurs": [{k: (dict(v) if isinstance(v, Counter) else v) for k, v in p.items() if k != "detail_sport"}
                                for p in sorted(players, key=lambda p: -p["selection"])]},
        "projection_saison": {"simulations": 20000, "matchs_restants": len(remaining),
                              "horizon": max((m["matchday"] for m in raw["remaining"] if isinstance(m["matchday"], int)), default=None),
                              "clubs": {t: {**v, "club": names.get(t, t)} for t, v in sim.items()}},
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--today", default=None)
    ap.add_argument("--season", type=int, default=2026)
    args = ap.parse_args(argv)
    today = date.fromisoformat(args.today) if args.today else date.today()
    out = build(today, args.season)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    pm = out["prochain_match"]
    print(f"xG-tirs : {out['xg_modele']}")
    for blk in [pm["coupe"]] + pm["championnat_matchs"]:
        if not blk:
            continue
        m, p = blk["match"], blk["prediction"]
        print(f"\n{m['date']} {m['home']} – {m['away']} : dom {p['dom']:.0%} · nul {p['nul']:.0%} · ext {p['ext']:.0%} ; "
              f"xG {p['xg_dom']} – {p['xg_ext']} ; scores {[s['score'] for s in p['scores'][:3]]}"
              + (f" ; qualification Yverdon {p['qualif_ext'] if m['away_id'] == YS else p['qualif_dom']:.0%}" if "qualif_dom" in p else ""))
        print("  Onze :", ", ".join(f"{r['creneau']} {r['name']}" for r in blk["onze"]))
    ys = out["projection_saison"]["clubs"][YS]
    print(f"\nProjection Yverdon : {ys['points_projetes']} points, rang moyen {ys['rang_moyen']}, P(1er) {ys['p_rang'][1]:.0%}")
    print(f"→ {OUT}")


if __name__ == "__main__":
    main()
