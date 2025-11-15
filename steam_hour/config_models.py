from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List

MAX_SIMULTANEOUS_GAMES = 33


class ConfigValidationError(ValueError):

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class AccountConfig:
    username: str = ""
    password: str = ""
    shared_secret: str | None = None
    api_key: str | None = None
    steam_id: str | None = None


@dataclass
class AppConfig:
    account: AccountConfig = field(default_factory=AccountConfig)
    games: List[int] = field(default_factory=list)
    language: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AppConfig":
        account_data = payload.get("account") or {}
        games = payload.get("games") or []

        account = AccountConfig(
            username=str(account_data.get("username") or ""),
            password=str(account_data.get("password") or ""),
            shared_secret=account_data.get("shared_secret") or None,
            api_key=account_data.get("api_key") or None,
            steam_id=str(account_data.get("steam_id") or "").strip() or None,
        )

        normalized_games: List[int] = []
        for entry in games:
            try:
                app_id = int(entry)
            except (TypeError, ValueError):
                continue
            if app_id > 0 and app_id not in normalized_games:
                normalized_games.append(app_id)

        language = str(payload.get("language") or "").strip()

        return cls(account=account, games=normalized_games[:MAX_SIMULTANEOUS_GAMES], language=language)

    def add_game(self, app_id: int) -> bool:
        if app_id <= 0 or app_id in self.games or len(self.games) >= MAX_SIMULTANEOUS_GAMES:
            return False
        self.games.append(app_id)
        return True

    def remove_game(self, app_id: int) -> bool:
        if app_id not in self.games:
            return False
        self.games.remove(app_id)
        return True

    def clear_games(self) -> None:
        self.games.clear()

    def validate(self) -> None:
        if not self.account.username:
            raise ConfigValidationError("errors.missing_username")
        if not self.account.password:
            raise ConfigValidationError("errors.missing_password")
        if not self.games:
            raise ConfigValidationError("errors.missing_games")

    @staticmethod
    def ensure_file(path: Path) -> AppConfig:
        if not path.exists():
            default_config = AppConfig()
            path.write_text(json.dumps(default_config.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
            return default_config
        return AppConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))


