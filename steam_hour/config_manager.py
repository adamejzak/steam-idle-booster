from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .config_models import AppConfig


class ConfigManager:

    def __init__(self, file_path: str | Path = "config.json") -> None:
        self.path = Path(file_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._config: Optional[AppConfig] = None

    @property
    def config(self) -> AppConfig:
        if self._config is None:
            self._config = self.load()
        return self._config

    def load(self) -> AppConfig:
        if not self.path.exists():
            config = AppConfig()
            self.save(config)
            return config

        data = json.loads(self.path.read_text(encoding="utf-8"))
        return AppConfig.from_dict(data)

    def save(self, config: Optional[AppConfig] = None) -> None:
        target = config or self._config
        if target is None:
            target = AppConfig()
        self.path.write_text(json.dumps(target.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        self._config = target

    def reset(self) -> AppConfig:
        self._config = AppConfig()
        self.save(self._config)
        return self._config


