"""Configuration du club : tout ce qui est propre à un club vit dans club.json.

Le moteur (model.py, fan_dna.py, scout.py, recommend.py, dashboard_template.html)
est le même d'un club à l'autre. club.json, à la racine du dossier du club (à côté
de data/), donne :

- name, short, search_name (recherche Transfermarkt), transfermarkt_club_id (option) ;
- home_league : code Transfermarkt de la ligue de référence (coefficient 1,0) ;
- squad_target : effectif cible par ligne (GK, DEF, MID, ATT) ;
- géographie du fan fit : bassin (villes du cœur), canton, region (Romandie), frontalier ;
- club_needles / club_exclude : chaînes qui reconnaissent le club dans les transferts
  et les clubs formateurs (et celles qui l'excluent, ex. « ouchy » pour Lausanne) ;
- recruit_campaign_keywords : mots qui repèrent l'annonce des recrues dans les campagnes ;
- scout : périmètre par défaut du scan ; dashboard : bornes des curseurs ;
- labels et rationale : libellés du tableau de bord et textes de l'ADN fan.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLUB_FILE = ROOT / "club.json"

REQUIRED = ("name", "short", "search_name", "home_league", "squad_target",
            "club_needles", "bassin", "canton", "region")


def load(path: Path = CLUB_FILE) -> dict:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{path} introuvable : chaque dossier de club a son club.json (voir README).")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if k not in cfg]
    if missing:
        raise ValueError(f"{path} : clés manquantes {missing}")
    for k in ("bassin", "canton", "region", "frontalier", "club_needles", "club_exclude"):
        cfg[k] = [s.strip().lower() for s in cfg.get(k) or []]
    cfg.setdefault("search_country", "Switzerland")
    cfg.setdefault("transfermarkt_club_id", None)
    cfg.setdefault("recruit_campaign_keywords", ["RECRU"])
    cfg.setdefault("scout", {})
    cfg.setdefault("dashboard", {})
    cfg.setdefault("rationale", {})
    labels = cfg.setdefault("labels", {})
    labels.setdefault("league_ref", cfg["home_league"])
    labels.setdefault("bassin", "Bassin")
    labels.setdefault("canton", "Canton")
    labels.setdefault("forme_club", "Formé au club")
    labels.setdefault("ancien", f"Ancien du {cfg['short']}")
    labels.setdefault("title", f"Moneyball {cfg['name']}")
    labels.setdefault("eyebrow", f"{cfg['name']} · Cellule recrutement")
    labels.setdefault("cockpit", f"Cockpit Fanbase Manager {cfg['name']}")
    return cfg


CLUB = load()


def is_club(name: str | None, cfg: dict = CLUB) -> bool:
    """Vrai si un nom de club (transfert, club formateur, club actuel) désigne le club."""
    n = (name or "").lower()
    return any(k in n for k in cfg["club_needles"]) and not any(x in n for x in cfg["club_exclude"])


def public_config(cfg: dict = CLUB) -> dict:
    """Ce que le tableau de bord doit connaître du club (injecté dans recommendations.json)."""
    return {k: cfg[k] for k in ("name", "short", "home_league", "squad_target", "labels", "dashboard")}
