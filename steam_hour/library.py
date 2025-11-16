from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import requests

STEAM_API_URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
STEAM_VANITY_URL = "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"
LIBRARY_ERROR_UNAUTHORIZED = "LIBRARY_UNAUTHORIZED"


class SteamLibraryError(RuntimeError):
    """Raised when Steam library data cannot be fetched or parsed."""
@dataclass
class OwnedGame:
    app_id: int
    name: str


class SteamLibraryFetcher:
    def __init__(self, api_key: str, steam_id: str, timeout: float = 20.0) -> None:
        if not api_key or not steam_id:
            raise SteamLibraryError("Missing API key or SteamID64.")
        self.api_key = api_key
        self.steam_id = steam_id
        self.timeout = timeout

    def fetch_owned_games(self) -> List[OwnedGame]:
        params = {
            "key": self.api_key,
            "steamid": self.steam_id,
            "include_appinfo": True,
            "include_played_free_games": True,
        }
        try:
            response = requests.get(STEAM_API_URL, params=params, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 401:
                raise SteamLibraryError(LIBRARY_ERROR_UNAUTHORIZED) from exc
            raise SteamLibraryError(f"HTTP {status_code}: {exc}") from exc
        except requests.RequestException as exc:
            raise SteamLibraryError(str(exc)) from exc
        except ValueError as exc:
            raise SteamLibraryError("Invalid JSON in Steam response.") from exc

        games_raw = (payload.get("response") or {}).get("games") or []
        owned_games: List[OwnedGame] = []
        for entry in games_raw:
            try:
                app_id = int(entry.get("appid"))
            except (TypeError, ValueError):
                continue
            name = entry.get("name") or f"App {app_id}"
            owned_games.append(OwnedGame(app_id=app_id, name=name))
        owned_games.sort(key=lambda g: g.name.lower())
        return owned_games

    def resolve_steam_id(self, vanity_name: str) -> str:
        params = {
            "key": self.api_key,
            "vanityurl": vanity_name.strip(),
        }
        try:
            response = requests.get(STEAM_VANITY_URL, params=params, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 401:
                raise SteamLibraryError(LIBRARY_ERROR_UNAUTHORIZED) from exc
            raise SteamLibraryError(f"HTTP {status_code}: {exc}") from exc
        except requests.RequestException as exc:
            raise SteamLibraryError(str(exc)) from exc
        except ValueError as exc:
            raise SteamLibraryError("Invalid JSON in Steam response.") from exc
        result = (payload.get("response") or {}).get("steamid")
        if not result:
            raise SteamLibraryError("Vanity URL not found.")
        return str(result)


def chunk_list(items: Sequence[OwnedGame], columns: int) -> List[List[OwnedGame]]:
    if columns <= 0:
        columns = 1
    rows: List[List[OwnedGame]] = []
    for index in range(0, len(items), columns):
        rows.append(list(items[index : index + columns]))
    return rows

