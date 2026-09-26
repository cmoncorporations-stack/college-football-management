"""ADN fan d'Yverdon Sport, dérivé du cockpit Fanbase Manager (Brevo + Metricool).

Le cockpit ne dit rien des joueurs : il dit ce qui fait réagir la fanbase.
Ce module traduit ces signaux en pondérations du score d'adéquation fan
(fan fit), avec la justification chiffrée de chaque pondération. Les
pondérations bougent quand le cockpit est rafraîchi.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

COCKPIT = Path(__file__).resolve().parent.parent / "data" / "fanbase_cockpit.json"

# Pondérations de base avant modulation par les données du club.
BASE_WEIGHTS = {
    "ancrage": 0.30,        # joueur du bassin / de Romandie
    "francophonie": 0.15,   # capacité à parler aux fans (tous les envois sont en FR)
    "fidelite": 0.20,       # figure durable à laquelle s'attacher
    "media": 0.20,          # potentiel de contenu (réseaux, spectacle)
    "lien_club": 0.15,      # passé au club / connaissance du foot suisse
}


def _growth(series: list) -> float:
    first, last = series[0][1], series[-1][1]
    return (last - first) / first * 100 if first else 0.0


def load_cockpit(path: Path = COCKPIT) -> dict:
    return json.loads(Path(path).read_text())


def derive_fan_dna(cockpit: dict) -> dict:
    pen = cockpit["penetration"]
    brevo = cockpit["brevo"]
    game = cockpit["game"]
    camps = [c for c in cockpit["campagnes"] if c["delivered"] >= 100]

    cats = {c["nom"]: c["pct"] for c in brevo["categories"]}
    new_lead_pct = cats.get("New Lead", 0.0)
    super_fan_pct = cats.get("Super Fan", 0.0)

    avg_open = mean(c["ouverture"] for c in camps) if camps else 40.0
    recruit_camps = [c for c in camps if "RECRU" in c["nom"].upper()]
    merch_camps = [c for c in camps if "MERCH" in c["nom"].upper()]
    recruit_open = mean(c["ouverture"] for c in recruit_camps) if recruit_camps else avg_open
    merch_open = mean(c["ouverture"] for c in merch_camps) if merch_camps else avg_open

    # 1) Ancrage : plus la pénétration du bassin est forte, plus le club a un
    #    noyau local à protéger ; au-delà de 18 % (bande « correcte ») on renforce.
    ancrage_mult = 1 + max(-0.3, min(0.5, (pen["indice"] - 18) / 30))
    # 2) Fidélité : une base dominée par les New Leads a besoin de visages
    #    durables pour faire monter les fans dans l'entonnoir.
    fidelite_mult = 1 + max(-0.3, min(0.5, (new_lead_pct - 50) / 60))
    # 3) Média : si l'annonce des recrues ouvre mieux que la moyenne, les
    #    transferts sont un moteur d'audience, on valorise le joueur « contenu ».
    media_mult = max(0.7, min(1.5, recruit_open / avg_open))
    # 4) Francophonie et lien club restent à leur poids de base.
    mults = {"ancrage": ancrage_mult, "francophonie": 1.0, "fidelite": fidelite_mult,
             "media": media_mult, "lien_club": 1.0}
    raw = {k: BASE_WEIGHTS[k] * mults[k] for k in BASE_WEIGHTS}
    total = sum(raw.values())
    weights = {k: round(v / total, 3) for k, v in raw.items()}

    igM, igF = cockpit["series"]["igM"], cockpit["series"]["igF"]
    signals = {
        "penetration_indice": pen["indice"],
        "penetration_coeur": pen["coeur"]["indice"],
        "bassin": pen["bassin"],
        "population_bassin": pen["population"],
        "exploitables": brevo["exploitables"],
        "new_lead_pct": new_lead_pct,
        "super_fan_pct": super_fan_pct,
        "ouverture_moyenne": round(avg_open, 1),
        "ouverture_recrues": round(recruit_open, 1),
        "ouverture_merch": round(merch_open, 1),
        "engagement_ig_club": game["engIGclub"],
        "croissance_ig_masculin_180j": round(_growth(igM), 1),
        "croissance_ig_feminin_180j": round(_growth(igF), 1),
        "audience_sociale": game["audienceTotale"],
    }
    rationale = {
        "ancrage": f"Indice de pénétration {pen['indice']} % sur le bassin {pen['bassin']} "
                   f"({pen['coeur']['indice']} % sur le cœur Jura-Nord vaudois) : la fanbase est "
                   f"d'abord locale, un joueur du coin la mobilise.",
        "francophonie": "Toutes les campagnes Brevo sont rédigées en français : un joueur "
                        "francophone porte directement les contenus et les événements fans.",
        "fidelite": f"{new_lead_pct} % de la base est en New Lead et {super_fan_pct} % en Super Fan : "
                    f"le club doit convertir, il lui faut des visages qui restent.",
        "media": f"L'annonce des recrues a ouvert à {recruit_open:.1f} % contre {avg_open:.1f} % de "
                 f"moyenne : le mercato est un moteur d'audience ; engagement Instagram "
                 f"{game['engIGclub']} %.",
        "lien_club": "Un ancien du club ou un joueur rodé au football suisse s'intègre et se "
                     "raconte plus vite auprès des supporters.",
    }
    return {"weights": weights, "multiplicateurs": {k: round(v, 3) for k, v in mults.items()},
            "signals": signals, "rationale": rationale, "releve": cockpit.get("genere")}


if __name__ == "__main__":
    print(json.dumps(derive_fan_dna(load_cockpit()), ensure_ascii=False, indent=2))
