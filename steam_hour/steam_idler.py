from __future__ import annotations

import getpass
import logging
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Iterable, List

from steam.client import SteamClient
from steam.enums import EPersonaState, EResult
from steam.guard import generate_twofactor_code

from .config_models import MAX_SIMULTANEOUS_GAMES, AppConfig
from .localization import Localization

LOG = logging.getLogger("steam_hour.idler")


class SteamRunError(RuntimeError):
    """Raised when the idler cannot continue."""


class SteamLoginError(RuntimeError):
    """Raised when Steam login fails."""


class SteamIdler:
    def __init__(
        self,
        config: AppConfig,
        localization: Localization | None = None,
        credential_dir: str | Path | None = None,
    ) -> None:
        self.config = config
        self.localization = localization or Localization(config.language or None)
        self.credential_dir = Path(credential_dir or Path(".steam_credentials"))
        self.credential_dir.mkdir(parents=True, exist_ok=True)
        self.client = SteamClient()
        self.client.set_credential_location(str(self.credential_dir))

    def start(self) -> None:
        games = self._prepare_games(self.config.games)
        if not games:
            raise SteamRunError(self.t("steam.error.no_games"))

        games_preview = ", ".join(map(str, games))
        print(f"\n{self.t('steam.preparing', count=len(games), games=games_preview)}")
        LOG.info("Logowanie do Steam jako %s", self.config.account.username)
        print(self.t("steam.connecting"))
        try:
            self._login_loop()
        except KeyboardInterrupt as exc: 
            raise SteamRunError(self.t("steam.interrupt")) from exc
        LOG.info("Ustawianie statusu Online i uruchamianie %d gier.", len(games))
        self._set_persona_online()
        self.client.games_played(games)
        print(self.t("steam.launched", games=games_preview))

        start_time = time.monotonic()
        stop_event = threading.Event()
        clock_thread = threading.Thread(
            target=self._run_clock,
            args=(len(games), start_time, stop_event),
            daemon=True,
        )
        clock_thread.start()
        try:
            print()
            input(self.t("steam.prompt.stop"))
            stop_event.set()
        except KeyboardInterrupt:
            stop_event.set()
            print(f"\n{self.t('steam.interrupt')}")
        finally:
            clock_thread.join()
            self.client.logout()
            print(self.t("steam.logout"))
            LOG.info("Wylogowano ze Steam.")

    def _prepare_games(self, games: Iterable[int]) -> List[int]:
        unique: List[int] = []
        for app_id in games:
            if app_id not in unique:
                unique.append(app_id)
            if len(unique) >= MAX_SIMULTANEOUS_GAMES:
                break
        return unique

    def _login_loop(self) -> None:
        username = self.config.account.username
        password = self.config.account.password
        shared_secret = self.config.account.shared_secret

        if not username or not password:
            raise SteamLoginError(self.t("steam.login.credentials_missing"))

        auth_code: str | None = None
        two_factor_code: str | None = None

        while True:
            if shared_secret and not two_factor_code:
                try:
                    two_factor_code = generate_twofactor_code(shared_secret)
                except Exception as exc:
                    LOG.warning("Nie udało się wygenerować kodu 2FA: %s", exc)
                    two_factor_code = None
            result = self.client.login(
                username=username,
                password=password,
                auth_code=auth_code,
                two_factor_code=two_factor_code,
            )
            if result == EResult.OK:
                print(self.t("steam.login.success"))
                return
            if result == EResult.AccountLogonDenied:
                print(self.t("steam.login.email_needed"))
                auth_code = input(self.t("steam.login.email_prompt")).strip()
                two_factor_code = None
                continue
            if result == EResult.AccountLoginDeniedNeedTwoFactor:
                print(self.t("steam.login.twofactor_needed"))
                if not shared_secret:
                    two_factor_code = input(self.t("steam.login.twofactor_prompt")).strip()
                else:
                    two_factor_code = generate_twofactor_code(shared_secret)
                auth_code = None
                continue
            if result == EResult.InvalidPassword:
                print(self.t("steam.login.invalid_password_prompt"))
                password = getpass.getpass(self.t("steam.login.invalid_password_input"))
                self.config.account.password = password
                continue
            raise SteamLoginError(self.t("steam.login.error_generic", result=result.name))

    def _set_persona_online(self) -> None:
        """Try multiple APIs to set persona so the account appears online."""
        set_persona = getattr(self.client, "set_persona", None)
        if callable(set_persona):
            try:
                set_persona(state=EPersonaState.Online)
                return
            except TypeError:
                set_persona(EPersonaState.Online)
                return

        friends = getattr(self.client, "friends", None)
        if friends is not None:
            friend_set_persona = getattr(friends, "set_persona", None)
            if callable(friend_set_persona):
                friend_set_persona(state=EPersonaState.Online)
                return

        LOG.warning(self.t("steam.warning.persona_missing"))

    def _run_clock(self, game_count: int, start_time: float, stop_event: threading.Event) -> None:
        effective_games = max(1, game_count)
        print(self.t("steam.running"))
        while not stop_event.wait(1):
            total_seconds = int((time.monotonic() - start_time) * effective_games)
            formatted = str(timedelta(seconds=total_seconds))
            print(f"\r{self.t('steam.clock', time=formatted)}   ", end="", flush=True)

        total_seconds = int((time.monotonic() - start_time) * effective_games)
        formatted = str(timedelta(seconds=total_seconds))
        print(f"\r{self.t('steam.clock', time=formatted)}   ")

    def t(self, key: str, **kwargs: object) -> str:
        return self.localization.translate(key, **kwargs)


