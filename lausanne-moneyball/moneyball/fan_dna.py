"""ADN fan du club, dérivé du cockpit Fanbase Manager (Brevo + Metricool).

Le cockpit ne dit rien des joueurs : il dit ce qui fait réagir la fanbase.
Ce module traduit ces signaux en pondérations du score d'adéquation fan
(fan fit), avec la justification chiffrée de chaque pondération. Les
pondérations bougent quand le cockpit est rafraîchi. Quand un signal manque
(pas de campagne « recrues », scoring non calibré…), le multiplicateur retombe
à 1,0 et la justification le dit.

    python -m moneyball.fan_dna                          # ADN fan du cockpit courant
    python -m moneyball.fan_dna --import-artifact a.html # artefact Fanbase Manager → data/fanbase_cockpit.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from statistics import mean

from .club import CLUB, ROOT

COCKPIT = ROOT / "data" / "fanbase_cockpit.json"

# Pondérations de base avant modulation par les données du club.
BASE_WEIGHTS = {
    "ancrage": 0.30,        # joueur du bassin / de Romandie
    "francophonie": 0.15,   # capacité à parler aux fans
    "fidelite": 0.20,       # figure durable à laquelle s'attacher
    "media": 0.20,          # potentiel de contenu (réseaux, spectacle)
    "lien_club": 0.15,      # passé au club / connaissance du foot suisse
}

# Textes par défaut ; club.json → rationale les remplace (placeholders : voir _values).
DEFAULT_RATIONALE = {
    "ancrage": "Indice de pénétration {indice_fr} % sur le bassin {bassin} ({coeur_indice_fr} % en lecture "
               "{coeur_nom}) : la fanbase est d'abord locale, un joueur du coin la mobilise.",
    "francophonie": "Un joueur francophone porte directement les contenus et les événements fans.",
    "fidelite": "{new_lead_pct_fr} % de la base est en New Lead et {super_fan_pct_fr} % en Super Fan : "
                "le club doit convertir, il lui faut des visages qui restent.",
    "media": "L'annonce des recrues a ouvert à {recruit_open_fr} % contre {avg_open_fr} % de moyenne : "
             "le mercato est un moteur d'audience ; engagement Instagram {eng_ig_fr} %.",
    "lien_club": "Un ancien du club ou un joueur rodé au football suisse s'intègre et se raconte plus "
                 "vite auprès des supporters.",
}
CATEGORY_NAMES = {"NL": "New Lead", "W": "Warm", "Q": "Qualified", "SQ": "Sales Qualified", "SF": "Super Fan"}


def _growth(series: list) -> float | None:
    if not series:
        return None
    first, last = series[0][1], series[-1][1]
    return (last - first) / first * 100 if first else 0.0


def _fr(x, d: int = 1) -> str:
    return "n/d" if x is None else f"{x:.{d}f}".replace(".", ",")


# --------------------------------------------------------------------------- import des cockpits
def extract_payload(html: str) -> dict:
    """Données d'un artefact Fanbase Manager : bloc <script id="payload"> ou `window.__FBM__ = {...}`."""
    m = re.search(r'<script[^>]*id="payload"[^>]*>(.*?)</script>', html, re.S)
    if m:
        return json.loads(m.group(1))
    m = re.search(r"window\.__FBM__\s*=\s*", html)
    if not m:
        raise ValueError("ni <script id=\"payload\"> ni window.__FBM__ dans l'artefact")
    obj, _ = json.JSONDecoder().raw_decode(html, m.end())
    return obj


def from_fbm_payload(d: dict, source: str = "") -> dict:
    """Payload `window.__FBM__` (cockpits Fanbase Manager de septembre 2026, ex. LS) → schéma du cockpit YS.

    Le panel des autres clubs n'est pas repris : leurs indices sont confidentiels.
    """
    meta, pen, b = d["meta"], d["penetration"], d["brevo"]
    em, R, posts = d.get("emailing", {}), d.get("reseauxData", {}), d.get("posts", {})
    main = pen.get("canton") or pen.get("bassin") or {}
    coeur = pen.get("agglo") or pen.get("coeur") or {}

    def serie(k):
        n = R.get(k) or {}
        return [] if n.get("sansDonnees") else [[x[0], x[1]] for x in n.get("serie", [])]

    live = {k: n for k, n in R.items() if not n.get("sansDonnees")}
    base = b.get("pilotables") or sum((b.get("cats") or {}).values())
    categories = [{"nom": CATEGORY_NAMES[k], "n": v, "pct": round(v / base * 100, 1) if base else 0.0}
                  for k, v in (b.get("cats") or {}).items() if k in CATEGORY_NAMES]
    social = next((f["v"] for f in d.get("funnel", []) if f.get("k", "").startswith("Audience sociale")),
                  sum(n.get("abonnes") or 0 for n in live.values()))
    return {
        "_source": source or f"Artefact Fanbase Manager {meta.get('club')} — payload window.__FBM__",
        "_notes": ("Comptes sociaux du club, sans séparation masculin/féminin : rangés sous igM, fbM, li, ytM "
                   "(igF, fbF et postsF vides). Séries quotidiennes [date, abonnés] sur la fenêtre du cockpit "
                   "(seriesFenetreJours). postsM = publications Instagram listées par le cockpit "
                   "[date, interactions, portée]. Audience sociale cumulée, non dédupliquée."),
        "genere": meta.get("genere"),
        "series": {"igM": serie("IG"), "igF": [], "fbM": serie("FB"), "fbF": [], "li": serie("LI"), "ytM": serie("YT")},
        "seriesFenetreJours": len(serie("IG")) or None,
        "game": {
            "followers": {k: n.get("abonnes") for k, n in live.items()},
            "cibles": {k: n.get("cible") for k, n in live.items()},
            "audienceTotale": social,
            "audienceDedupliquee": False,
            "sansDonnees": [n.get("label") for n in R.values() if n.get("sansDonnees")],
            "attrs": d.get("attrs"), "ovr": d.get("ovr"), "division": d.get("division"),
            "engIGclub": posts.get("engIG"), "engLI": posts.get("engLI"),
            "postsIG90j": posts.get("igCount90j"), "postsLI90j": len(posts.get("li", [])),
        },
        "penetration": {
            "sport": "football", "fanShare": pen.get("fanRate"),
            "bassin": main.get("nom"), "population": main.get("pop"), "audienceActive": main.get("active"),
            "contacts": pen.get("numerateur"), "baseLabel": pen.get("baseNumerateur"),
            "indice": main.get("indice"), "niveau": main.get("niveau"), "cible": pen.get("cible"),
            "contactsPourCible": pen.get("contactsPourCible"),
            "coeur": {"nom": coeur.get("nom"), "population": coeur.get("pop"), "active": coeur.get("active"),
                      "indice": coeur.get("indice"), "niveau": coeur.get("niveau"),
                      "depasseGardeFou": coeur.get("depasseGardeFou", False)},
        },
        "brevo": {
            "contactsTotal": b.get("total"), "exploitables": b.get("exploitables"), "pilotables": b.get("pilotables"),
            "desinscrits": b.get("blacklist"), "completionGlobale": b.get("completionGlobal"),
            "completion": b.get("completion"), "categories": categories,
            "scoringExploitable": b.get("scoringExploitable", True),
            "score": {"median": b.get("scoreMedian"), "max": b.get("scoreMax"),
                      "nonNuls": b.get("scoresNonNuls"), "pctScores": b.get("pctScores")},
            "partenaires": b.get("partenaires"),
        },
        "croissance12m": {"debut": b.get("base12m"), "fin": b.get("exploitables"),
                          "ajoutes": (b.get("exploitables") or 0) - (b.get("base12m") or 0), "pct": b.get("croissance12m")},
        "campagnes": [{"date": c["d"], "nom": c["n"], "equipe": None, "delivered": c["del"],
                       "ouverture": c["ouv"], "clics": c["cli"]} for c in em.get("top", [])],
        # Le payload ne liste que les plus gros envois : la moyenne vient de l'agrégat du cockpit.
        "campagnesResume": {"n90j": em.get("nIn90d"), "delivres": em.get("delivered"),
                            "ouvertureMoyenne": em.get("openRateMoyen"), "ouverturePonderee": em.get("openRatePondere"),
                            "listeComplete": False},
        "postsM": [[p["d"].replace("-", ""), p.get("inter"), p.get("reach")] for p in posts.get("ig", [])],
        "postsF": [],
    }


def load_cockpit(path: Path = COCKPIT) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "meta" in data and "attrs" in data:     # payload brut d'un artefact : on le mappe
        return from_fbm_payload(data)
    return data


# --------------------------------------------------------------------------- ADN fan
def derive_fan_dna(cockpit: dict, club: dict = CLUB) -> dict:
    pen = cockpit.get("penetration") or {}
    brevo = cockpit.get("brevo") or {}
    game = cockpit.get("game") or {}
    camps = [c for c in cockpit.get("campagnes") or [] if (c.get("delivered") or 0) >= 100]
    resume = cockpit.get("campagnesResume") or {}
    coeur = pen.get("coeur") or {}
    fallback = {}

    cats = {c["nom"]: c["pct"] for c in brevo.get("categories") or []}
    scoring_ok = bool(cats) and brevo.get("scoringExploitable", True)
    new_lead_pct = cats.get("New Lead", 0.0)
    super_fan_pct = cats.get("Super Fan", 0.0)

    if resume.get("ouvertureMoyenne") is not None:
        avg_open = resume["ouvertureMoyenne"]
    else:
        avg_open = mean(c["ouverture"] for c in camps) if camps else 40.0
    keys = [k.upper() for k in club.get("recruit_campaign_keywords") or ["RECRU"]]
    recruit_camps = [c for c in camps if any(k in c["nom"].upper() for k in keys)]
    merch_camps = [c for c in camps if "MERCH" in c["nom"].upper()]
    recruit_open = mean(c["ouverture"] for c in recruit_camps) if recruit_camps else None
    merch_open = mean(c["ouverture"] for c in merch_camps) if merch_camps else avg_open

    # 1) Ancrage : plus la pénétration du bassin est forte, plus le club a un
    #    noyau local à protéger ; au-delà de 18 % (bande « correcte ») on renforce.
    if pen.get("indice") is not None:
        ancrage_mult = 1 + max(-0.3, min(0.5, (pen["indice"] - 18) / 30))
    else:
        ancrage_mult = 1.0
        fallback["ancrage"] = ("Le cockpit ne donne pas d'indice de pénétration : poids de base conservé "
                               "(multiplicateur 1,0).")
    # 2) Fidélité : une base dominée par les New Leads a besoin de visages
    #    durables pour faire monter les fans dans l'entonnoir.
    if scoring_ok:
        fidelite_mult = 1 + max(-0.3, min(0.5, (new_lead_pct - 50) / 60))
    else:
        fidelite_mult = 1.0
        score = brevo.get("score") or {}
        fallback["fidelite"] = (
            (f"CMON_SCORE n'est pas encore calibré ({_fr(score.get('pctScores'), 2)} % des contacts pilotables "
             f"scorés, score maximal {score.get('max')} alors que le palier Warm commence à 25) : "
             f"les {_fr(new_lead_pct)} % de New Leads sont mécaniques, pas un signal de la fanbase. ")
            if cats else "Le cockpit ne donne pas la répartition des catégories CMON_SCORE. "
        ) + "Poids de base conservé (multiplicateur 1,0)."
    # 3) Média : si l'annonce des recrues ouvre mieux que la moyenne, les
    #    transferts sont un moteur d'audience, on valorise le joueur « contenu ».
    if recruit_open is not None and avg_open:
        media_mult = max(0.7, min(1.5, recruit_open / avg_open))
    else:
        media_mult = 1.0
        fallback["media"] = (f"Aucune campagne d'annonce des recrues repérée dans le cockpit (mots-clés "
                             f"{', '.join(keys)}) : poids de base conservé (multiplicateur 1,0) ; "
                             f"engagement Instagram {_fr(game.get('engIGclub'), 2)} %.")
    # 4) Francophonie et lien club restent à leur poids de base.
    mults = {"ancrage": ancrage_mult, "francophonie": 1.0, "fidelite": fidelite_mult,
             "media": media_mult, "lien_club": 1.0}
    raw = {k: BASE_WEIGHTS[k] * mults[k] for k in BASE_WEIGHTS}
    total = sum(raw.values())
    weights = {k: round(v / total, 3) for k, v in raw.items()}

    series = cockpit.get("series") or {}
    igM, igF = series.get("igM") or [], series.get("igF") or []
    signals = {
        "penetration_indice": pen.get("indice"),
        "penetration_coeur": coeur.get("indice"),
        "bassin": pen.get("bassin"),
        "population_bassin": pen.get("population"),
        "exploitables": brevo.get("exploitables"),
        "new_lead_pct": new_lead_pct,
        "super_fan_pct": super_fan_pct,
        "ouverture_moyenne": round(avg_open, 1),
        "ouverture_recrues": None if recruit_open is None else round(recruit_open, 1),
        "ouverture_merch": round(merch_open, 1),
        "engagement_ig_club": game.get("engIGclub"),
        "croissance_ig_masculin_180j": None if _growth(igM) is None else round(_growth(igM), 1),
        "croissance_ig_feminin_180j": None if _growth(igF) is None else round(_growth(igF), 1),
        "audience_sociale": game.get("audienceTotale"),
    }
    if "scoringExploitable" in brevo:
        signals["scoring_exploitable"] = bool(brevo["scoringExploitable"])
    if "audienceDedupliquee" in game:
        signals["audience_dedupliquee"] = bool(game["audienceDedupliquee"])
    if cockpit.get("seriesFenetreJours"):
        signals["fenetre_series_jours"] = cockpit["seriesFenetreJours"]   # la croissance IG porte sur cette fenêtre

    values = {
        "indice": pen.get("indice"), "indice_fr": _fr(pen.get("indice")),
        "bassin": pen.get("bassin"), "coeur_indice": coeur.get("indice"), "coeur_indice_fr": _fr(coeur.get("indice")),
        "coeur_nom": coeur.get("nom"), "new_lead_pct": new_lead_pct, "new_lead_pct_fr": _fr(new_lead_pct),
        "super_fan_pct": super_fan_pct, "super_fan_pct_fr": _fr(super_fan_pct),
        "recruit_open": recruit_open, "recruit_open_fr": _fr(recruit_open),
        "avg_open": avg_open, "avg_open_fr": _fr(avg_open),
        "eng_ig": game.get("engIGclub"), "eng_ig_fr": _fr(game.get("engIGclub"), 2),
        "recruit_names": " ; ".join(f"{c['nom']}, {'.'.join(reversed(str(c['date'])[:10].split('-')))}"
                                    for c in recruit_camps),
        "mult_ancrage_fr": _fr(ancrage_mult, 2), "mult_media_fr": _fr(media_mult, 2),
    }
    texts = {**DEFAULT_RATIONALE, **(club.get("rationale") or {})}
    rationale = {k: fallback.get(k) or texts[k].format(**values) for k in BASE_WEIGHTS}
    return {"weights": weights, "multiplicateurs": {k: round(v, 3) for k, v in mults.items()},
            "signals": signals, "rationale": rationale, "releve": cockpit.get("genere")}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--import-artifact", metavar="HTML",
                    help="HTML d'un artefact Fanbase Manager : écrit data/fanbase_cockpit.json au schéma du moteur")
    ap.add_argument("--source", default="", help="Mention de provenance écrite dans _source")
    args = ap.parse_args(argv)
    if args.import_artifact:
        payload = extract_payload(Path(args.import_artifact).read_text(encoding="utf-8"))
        cockpit = payload if "meta" not in payload else from_fbm_payload(payload, args.source)
        COCKPIT.write_text(json.dumps(cockpit, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"→ {COCKPIT} (relevé du {cockpit.get('genere')})")
    print(json.dumps(derive_fan_dna(load_cockpit()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
