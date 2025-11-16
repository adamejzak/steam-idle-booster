from __future__ import annotations

import atexit
import getpass
import logging
import signal
import threading
import time
from datetime import timedelta
import json
import re
from pathlib import Path
from typing import Iterable, List, Protocol

import gevent
from steam.client import SteamClient
from steam.core.msg import MsgProto
from steam.enums import EPersonaState, EResult
from steam.enums.emsg import EMsg
from steam.guard import generate_twofactor_code

from .app_directory import SteamAppDirectory
from .config_models import MAX_SIMULTANEOUS_GAMES, AppConfig
from .localization import Localization

LOG = logging.getLogger("steam_hour.idler")
_ORIGINAL_LOGGING_SHUTDOWN = logging.shutdown
_ORIGINAL_HANDLER_RELEASE = logging.Handler.release
DEFAULT_CREDENTIAL_DIR = Path(".steam_credentials")
LOGIN_KEY_PREFIX = "login_key_"


def _sanitize_username(value: str) -> str:
    if not value:
        return "default"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


def _login_key_path(base_dir: Path, username: str) -> Path:
    return base_dir / f"{LOGIN_KEY_PREFIX}{_sanitize_username(username)}.json"


def _safe_logging_shutdown() -> None:
    try:
        _ORIGINAL_LOGGING_SHUTDOWN()
    except RuntimeError as exc:
        LOG.debug("Logging shutdown warning: %s", exc)


try:
    atexit.unregister(_ORIGINAL_LOGGING_SHUTDOWN)
except (AttributeError, ValueError):
    pass
atexit.register(_safe_logging_shutdown)


def _safe_handler_release(self) -> None:
    try:
        _ORIGINAL_HANDLER_RELEASE(self)
    except RuntimeError as exc:
        LOG.debug("Logging handler release warning: %s", exc)


logging.Handler.release = _safe_handler_release


class IdlerUI(Protocol):
    def log(self, message: str) -> None:
        ...

    def prompt_text(self, prompt: str) -> str:
        ...

    def prompt_secret(self, prompt: str) -> str:
        ...

    def start_stop_listener(self, prompt: str, stop_event: threading.Event) -> threading.Thread | None:
        ...

    def update_clock(self, message: str, *, final: bool = False) -> None:
        ...


class ConsoleIdlerUI:
    def log(self, message: str) -> None:
        print(message, flush=True)

    def prompt_text(self, prompt: str) -> str:
        return input(prompt)

    def prompt_secret(self, prompt: str) -> str:
        return getpass.getpass(prompt)

    def start_stop_listener(self, prompt: str, stop_event: threading.Event) -> threading.Thread:
        def wait_for_stop() -> None:
            try:
                print()
                input(prompt)
            except (KeyboardInterrupt, EOFError):
                pass
            finally:
                stop_event.set()

        thread = threading.Thread(target=wait_for_stop, daemon=True)
        thread.start()
        return thread

    def update_clock(self, message: str, *, final: bool = False) -> None:
        if final:
            print(f"\r{message}   ")
            return
        print(f"\r{message}   ", end="", flush=True)


class SteamRunError(RuntimeError):
    """Raised when the idler cannot continue."""


class SteamLoginError(RuntimeError):
    """Raised when Steam login fails."""


class PromptCancelled(RuntimeError):
    """Raised when a user cancels an interactive prompt."""


class SteamIdler:
    def __init__(
        self,
        config: AppConfig,
        localization: Localization | None = None,
        credential_dir: str | Path | None = None,
        ui: IdlerUI | None = None,
        keep_session: bool = False,
    ) -> None:
        self.config = config
        self.localization = localization or Localization(config.language or None)
        self.credential_dir = Path(credential_dir or DEFAULT_CREDENTIAL_DIR)
        self.credential_dir.mkdir(parents=True, exist_ok=True)
        self.client = SteamClient()
        self.client.set_credential_location(str(self.credential_dir))
        self.ui: IdlerUI = ui or ConsoleIdlerUI()
        self.app_directory = SteamAppDirectory()
        self.keep_session = keep_session
        self._last_username: str = ""

    def start(self) -> None:
        games = self._prepare_games(self.config.games)
        if not games:
            raise SteamRunError(self.t("steam.error.no_games"))

        games_preview = self._format_games_preview(games)
        self.ui.log("")
        self.ui.log(self.t("steam.preparing", count=len(games), games=games_preview))
        LOG.info("Logowanie do Steam jako %s", self.config.account.username)
        self.ui.log(self.t("steam.connecting"))
        try:
            self._login_loop()
        except KeyboardInterrupt as exc: 
            raise SteamRunError(self.t("steam.interrupt")) from exc
        LOG.info("Ustawianie statusu Online i uruchamianie %d gier.", len(games))
        self._set_persona_online()
        self._set_games_played(games)
        self.ui.log(self.t("steam.launched", games=games_preview))

        start_time = time.monotonic()
        stop_event = threading.Event()
        clock_thread = threading.Thread(
            target=self._run_clock,
            args=(len(games), start_time, stop_event),
            daemon=True,
        )
        clock_thread.start()

        prompt_thread = self.ui.start_stop_listener(self.t("steam.prompt.stop"), stop_event)

        interrupted = False

        def handle_sigint(_signum, _frame) -> None:
            nonlocal interrupted
            interrupted = True
            stop_event.set()

        original_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, handle_sigint)

        try:
            try:
                self._pump_gevent(stop_event)
            except KeyboardInterrupt:
                interrupted = True
                stop_event.set()
        finally:
            signal.signal(signal.SIGINT, original_sigint)
            if interrupted:
                self.ui.log("")
                self.ui.log(self.t("steam.interrupt"))
            if prompt_thread and not interrupted:
                prompt_thread.join()
            clock_thread.join()
            self._set_games_played([])
            if not self.keep_session:
                self.client.logout()
                self.ui.log(self.t("steam.logout"))
                LOG.info("Wylogowano ze Steam.")
            else:
                self.ui.log(self.t("steam.session.persisted"))
                LOG.info("Sesja Steam pozostaje aktywna.")
            self._shutdown_gevent_hub()

    def authenticate_only(self) -> None:
        """Log into Steam without starting the booster to refresh credentials."""
        self.ui.log(self.t("steam.connecting"))
        self._login_loop()
        self.ui.log(self.t("steam.login.auth_only_success"))
        if not self.keep_session:
            self.client.logout()
            self.ui.log(self.t("steam.logout"))
        else:
            self.ui.log(self.t("steam.session.persisted"))
        self._shutdown_gevent_hub()

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
        self._last_username = username or ""

        if not username or not password:
            raise SteamLoginError(self.t("steam.login.credentials_missing"))

        auth_code: str | None = None
        two_factor_code: str | None = None
        cached_key = self._load_login_key(username)
        if cached_key:
            result = self.client.login(username=username, login_key=cached_key)
            if result == EResult.OK:
                self._save_login_key(username)
                self.ui.log(self.t("steam.login.success"))
                return
            LOG.info("Stored login key invalid (result=%s); falling back to password.", result)
            self._delete_login_key(username)

        while True:
            if shared_secret and not two_factor_code:
                try:
                    two_factor_code = generate_twofactor_code(shared_secret)
                except Exception as exc:
                    LOG.warning("Failed to generate 2FA code: %s", exc)
                    two_factor_code = None
            result = self.client.login(
                username=username,
                password=password,
                auth_code=auth_code,
                two_factor_code=two_factor_code,
            )
            if result == EResult.OK:
                self._save_login_key(username)
                self.ui.log(self.t("steam.login.success"))
                return
            if result == EResult.AccountLogonDenied:
                self.ui.log(self.t("steam.login.email_needed"))
                try:
                    auth_code = self.ui.prompt_text(self.t("steam.login.email_prompt")).strip()
                except PromptCancelled as exc:
                    raise SteamRunError(self.t("steam.login.cancelled")) from exc
                two_factor_code = None
                continue
            if result == EResult.AccountLoginDeniedNeedTwoFactor:
                self.ui.log(self.t("steam.login.twofactor_needed"))
                if not shared_secret:
                    try:
                        two_factor_code = self.ui.prompt_text(self.t("steam.login.twofactor_prompt")).strip()
                    except PromptCancelled as exc:
                        raise SteamRunError(self.t("steam.login.cancelled")) from exc
                else:
                    two_factor_code = generate_twofactor_code(shared_secret)
                auth_code = None
                continue
            if result == EResult.InvalidPassword:
                self.ui.log(self.t("steam.login.invalid_password_prompt"))
                try:
                    password = self.ui.prompt_secret(self.t("steam.login.invalid_password_input"))
                except PromptCancelled as exc:
                    raise SteamRunError(self.t("steam.login.cancelled")) from exc
                self.config.account.password = password
                continue
            raise SteamLoginError(self.t("steam.login.error_generic", result=result.name))

    def _load_login_key(self, username: str) -> str | None:
        if not username:
            return None
        path = _login_key_path(self.credential_dir, username)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            try:
                path.unlink()
            except OSError:
                pass
            return None
        token = payload.get("login_key")
        if not token:
            try:
                path.unlink()
            except OSError:
                pass
            return None
        return str(token)

    def _save_login_key(self, username: str) -> None:
        if not username:
            return
        login_key = getattr(self.client, "login_key", None)
        if not login_key:
            return
        payload = {"login_key": login_key, "updated_at": int(time.time())}
        path = _login_key_path(self.credential_dir, username)
        try:
            path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            LOG.debug("Failed to persist login_key for %s", username)

    def _delete_login_key(self, username: str) -> None:
        if not username:
            return
        path = _login_key_path(self.credential_dir, username)
        try:
            path.unlink()
        except OSError:
            pass

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

        change_status = getattr(self.client, "change_status", None)
        if callable(change_status):
            try:
                change_status(persona_state=EPersonaState.Online)
                return
            except TypeError:
                change_status(state=EPersonaState.Online)
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
        self.ui.log(self.t("steam.running"))
        while not stop_event.wait(1):
            total_seconds = int((time.monotonic() - start_time) * effective_games)
            formatted = str(timedelta(seconds=total_seconds))
            self.ui.update_clock(self.t("steam.clock", time=formatted))

        total_seconds = int((time.monotonic() - start_time) * effective_games)
        formatted = str(timedelta(seconds=total_seconds))
        self.ui.update_clock(self.t("steam.clock", time=formatted), final=True)

    def _pump_gevent(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            gevent.sleep(0.2)

    def _set_games_played(self, games: List[int]) -> None:
        message = MsgProto(EMsg.ClientGamesPlayed)

        tracked_ids: list[int] = []

        for app_id in games:
            entry = message.body.games_played.add()
            entry.game_id = int(app_id)
            tracked_ids.append(int(app_id))

        self.client.send(message)

        try:
            self.client.current_games_played = tracked_ids
        except AttributeError:
            pass

    def t(self, key: str, **kwargs: object) -> str:
        return self.localization.translate(key, **kwargs)

    def _shutdown_gevent_hub(self) -> None:
        try:
            hub = gevent.get_hub(default=False)
        except Exception:
            return
        if not hub:
            return
        try:
            hub.destroy(destroy_loop=True)
        except TypeError:
            hub.destroy(True)
        except Exception as exc:
            LOG.debug("Gevent hub shutdown issue: %s", exc)

    def _format_games_preview(self, games: Iterable[int]) -> str:
        return ", ".join(self.app_directory.format_entry(app_id) for app_id in games)

    @staticmethod
    def has_cached_session(
        username: str | None = None, credential_dir: str | Path | None = None
    ) -> bool:
        target = Path(credential_dir or DEFAULT_CREDENTIAL_DIR)
        if not target.exists():
            return False
        if username:
            return _login_key_path(target, username).exists()
        try:
            return any(target.glob(f"{LOGIN_KEY_PREFIX}*.json"))
        except OSError:
            return False

    @staticmethod
    def clear_cached_session(
        username: str | None = None, credential_dir: str | Path | None = None
    ) -> None:
        target = Path(credential_dir or DEFAULT_CREDENTIAL_DIR)
        if not target.exists():
            return
        if username:
            path = _login_key_path(target, username)
            try:
                path.unlink()
            except OSError:
                pass
            return
        for entry in list(target.glob(f"{LOGIN_KEY_PREFIX}*.json")):
            try:
                entry.unlink()
            except OSError:
                continue



