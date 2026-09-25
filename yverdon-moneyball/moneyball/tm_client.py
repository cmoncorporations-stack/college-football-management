"""Client minimal pour transfermarkt-api (https://github.com/felipeall/transfermarkt-api).

Aucune dépendance externe : urllib + cache disque JSON. L'instance publique
(https://transfermarkt-api.fly.dev) est limitée en débit ; pour un usage
régulier, héberger sa propre instance (docker run -p 8000:8000 ...) et
définir TM_API_URL=http://localhost:8000.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://transfermarkt-api.fly.dev"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"


class TransfermarktClient:
    def __init__(self, base_url: str | None = None, cache_dir: Path = CACHE_DIR,
                 cache_ttl_h: float = 72, min_interval_s: float = 1.6, retries: int = 4):
        self.base_url = (base_url or os.environ.get("TM_API_URL") or DEFAULT_URL).rstrip("/")
        self.cache_dir = Path(cache_dir)
        self.cache_ttl_s = cache_ttl_h * 3600
        self.min_interval_s = min_interval_s
        self.retries = retries
        self._last_call = 0.0
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- plomberie ---------------------------------------------------------
    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".json")

    def get(self, path: str, **params) -> dict:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        cached = self._cache_path(url)
        if cached.exists() and time.time() - cached.stat().st_mtime < self.cache_ttl_s:
            return json.loads(cached.read_text())

        delay = 2.0
        for attempt in range(self.retries + 1):
            wait = self.min_interval_s - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
            try:
                req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                           "User-Agent": "yverdon-moneyball/1.0"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode())
                cached.write_text(json.dumps(data, ensure_ascii=False))
                return data
            except urllib.error.HTTPError as e:
                # 429 (rate limit) et 5xx : on retente ; 4xx : erreur définitive
                if e.code not in (429, 500, 502, 503, 504) or attempt == self.retries:
                    raise
            except urllib.error.URLError:
                if attempt == self.retries:
                    raise
            time.sleep(delay)
            delay *= 2
        raise RuntimeError("unreachable")

    # -- endpoints ---------------------------------------------------------
    def search_club(self, name: str) -> list[dict]:
        return self.get(f"/clubs/search/{urllib.parse.quote(name)}").get("results", [])

    def club_profile(self, club_id: str) -> dict:
        return self.get(f"/clubs/{club_id}/profile")

    def club_players(self, club_id: str, season_id: str | None = None) -> list[dict]:
        return self.get(f"/clubs/{club_id}/players", season_id=season_id).get("players", [])

    def competition_clubs(self, competition_id: str, season_id: str | None = None) -> dict:
        return self.get(f"/competitions/{competition_id}/clubs", season_id=season_id)

    def player_profile(self, player_id: str) -> dict:
        return self.get(f"/players/{player_id}/profile")

    def player_stats(self, player_id: str) -> list[dict]:
        return self.get(f"/players/{player_id}/stats").get("stats", [])

    def player_market_value(self, player_id: str) -> dict:
        return self.get(f"/players/{player_id}/market_value")

    def player_transfers(self, player_id: str) -> dict:
        return self.get(f"/players/{player_id}/transfers")

    def player_injuries(self, player_id: str) -> list[dict]:
        return self.get(f"/players/{player_id}/injuries").get("injuries", [])
