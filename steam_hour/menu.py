from __future__ import annotations

import getpass
from typing import Callable

from .config_manager import ConfigManager
from .config_models import MAX_SIMULTANEOUS_GAMES, AppConfig
from .library import SteamLibraryError, SteamLibraryFetcher
from .localization import Localization, SUPPORTED_LANGUAGES
from .steam_idler import SteamIdler, SteamLoginError, SteamRunError


class InteractiveMenu:

    def __init__(self, config_manager: ConfigManager) -> None:
        self.config_manager = config_manager
        self.config: AppConfig = config_manager.config
        self.localization = Localization(self.config.language or None)
        self.main_actions: dict[str, tuple[str, Callable[[], None]]] = {
            "1": ("menu.option.account", self.run_account_menu),
            "2": ("menu.option.games", self.run_games_menu),
            "3": ("menu.option.language", self.change_language),
            "4": ("menu.option.run", self.start_idler),
            "0": ("menu.option.exit", self.exit_menu),
        }
        self._running = False

    def t(self, key: str, **kwargs: object) -> str:
        return self.localization.translate(key, **kwargs)

    def run(self) -> None:
        self._running = True
        while self._running:
            self.print_main_menu()
            try:
                choice = input(self.t("prompt.choose_option")).strip()
            except (KeyboardInterrupt, EOFError):
                print(self.t("general.interrupted_exit"))
                break
            action = self.main_actions.get(choice, (None, None))[1]
            if not action:
                print(self.t("general.unknown_option"))
                continue
            try:
                action()
            except (SteamLoginError, SteamRunError, ValueError) as exc:
                print(self.t("general.error", message=exc))
            except (KeyboardInterrupt, EOFError):
                print(self.t("general.interrupted_exit"))
                self._running = False

    def print_main_menu(self) -> None:
        print(self.t("menu.banner"))
        account_ok = bool(self.config.account.username and self.config.account.password)
        account_status = self.t("menu.status.account_ok") if account_ok else self.t("menu.status.account_missing")
        games_status = f"{len(self.config.games)}/{MAX_SIMULTANEOUS_GAMES}"
        account_display = f"{account_status:>12}"
        print(self.t("menu.status", account=account_display, games=games_status))
        print(f"\n{self.t('menu.section_main')}")
        for key, (label_key, _) in self.main_actions.items():
            print(f"  {key}. {self.t(label_key)}")

    def run_account_menu(self) -> None:
        actions: dict[str, tuple[str, Callable[[], None]]] = {
            "1": ("account.option.summary", self.show_account_summary),
            "2": ("account.option.username", self.set_username),
            "3": ("account.option.password", self.set_password),
            "4": ("account.option.secret", self.set_shared_secret),
            "5": ("account.option.api_key", self.set_api_key),
            "6": ("account.option.steam_id", self.set_steam_id),
            "7": ("account.option.reset", self.reset_config),
            "8": ("account.option.language", self.change_language),
        }
        pending_action: Callable[[], None] | None = None
        while True:
            print(self.t("account.menu_banner"))
            for key, (label_key, _) in actions.items():
                print(f"  {key}. {self.t(label_key)}")
            print(f"  0. {self.t('general.back')}")
            if pending_action:
                if not self._execute_pending_action(pending_action):
                    return
                pending_action = None
            try:
                choice = input(self.t("prompt.choose_option")).strip()
            except (KeyboardInterrupt, EOFError):
                print(self.t("general.interrupted_back"))
                return
            if choice == "0":
                return
            action = actions.get(choice)
            if not action:
                print(self.t("general.unknown_option"))
                continue
            pending_action = action[1]

    def run_games_menu(self) -> None:
        actions: dict[str, tuple[str, Callable[[], None]]] = {
            "1": ("games.option.list", self.show_games_summary),
            "2": ("games.option.add", self.add_game),
            "3": ("games.option.remove", self.remove_game),
            "4": ("games.option.clear", self.clear_games),
            "5": ("games.option.library", self.add_from_library),
        }
        pending_action: Callable[[], None] | None = None
        while True:
            print(self.t("games.menu_banner"))
            for key, (label_key, _) in actions.items():
                print(f"  {key}. {self.t(label_key)}")
            print(f"  0. {self.t('general.back')}")
            if pending_action:
                if not self._execute_pending_action(pending_action):
                    return
                pending_action = None
            try:
                choice = input(self.t("prompt.choose_option")).strip()
            except (KeyboardInterrupt, EOFError):
                print(self.t("general.interrupted_back"))
                return
            if choice == "0":
                return
            action = actions.get(choice)
            if not action:
                print(self.t("general.unknown_option"))
                continue
            pending_action = action[1]

    def show_account_summary(self) -> None:
        hidden_password = "*" * len(self.config.account.password)
        username = self.config.account.username or self.t("general.not_set")
        password_display = hidden_password or self.t("general.not_set")
        secret_display = self.config.account.shared_secret or self.t("account.summary.secret_missing")
        print(f"\n{self.t('account.summary.title')}")
        print(self.t("account.summary.login", value=username))
        print(self.t("account.summary.password", value=password_display))
        print(self.t("account.summary.secret", value=secret_display))

    def show_games_summary(self) -> None:
        if not self.config.games:
            print(f"\n{self.t('games.summary.empty')}")
            return
        games = ", ".join(map(str, self.config.games))
        print(f"\n{self.t('games.summary.title')}")
        print(self.t("games.summary.count", count=len(self.config.games), max=MAX_SIMULTANEOUS_GAMES))
        print(self.t("games.summary.items", items=games))

    def set_username(self) -> None:
        username = input(self.t("account.prompt.username")).strip()
        if not username:
            raise ValueError(self.t("account.error.username_required"))
        self.config.account.username = username
        self.config_manager.save(self.config)
        print(self.t("account.saved.username"))

    def set_password(self) -> None:
        password = getpass.getpass(self.t("account.prompt.password")).strip()
        if not password:
            raise ValueError(self.t("account.error.password_required"))
        self.config.account.password = password
        self.config_manager.save(self.config)
        print(self.t("account.saved.password"))

    def set_shared_secret(self) -> None:
        secret = input(self.t("account.prompt.secret")).strip()
        self.config.account.shared_secret = secret or None
        self.config_manager.save(self.config)
        print(self.t("account.saved.secret"))

    def set_api_key(self) -> None:
        api_key = input(self.t("account.prompt.api_key")).strip()
        if not api_key:
            raise ValueError(self.t("account.error.api_key_required"))
        self.config.account.api_key = api_key
        self.config_manager.save(self.config)
        print(self.t("account.saved.api_key"))

    def set_steam_id(self) -> None:
        steam_id = input(self.t("account.prompt.steam_id")).strip()
        if not steam_id.isdigit():
            raise ValueError(self.t("account.error.steam_id_required"))
        self.config.account.steam_id = steam_id
        self.config_manager.save(self.config)
        print(self.t("account.saved.steam_id"))

    def add_game(self) -> None:
        if len(self.config.games) >= MAX_SIMULTANEOUS_GAMES:
            raise ValueError(self.t("games.error.full"))
        self.show_games_summary()
        raw = input(self.t("games.prompt.add")).strip()
        try:
            app_id = int(raw)
        except ValueError:
            raise ValueError(self.t("games.error.appid_integer"))
        if app_id <= 0:
            raise ValueError(self.t("games.error.appid_positive"))
        if app_id in self.config.games:
            raise ValueError(self.t("games.error.appid_exists"))
        self.config.games.append(app_id)
        self.config_manager.save(self.config)
        print(self.t("games.added"))
        self.show_games_summary()

    def remove_game(self) -> None:
        self.show_games_summary()
        if not self.config.games:
            return
        raw = input(self.t("games.prompt.remove")).strip()
        try:
            app_id = int(raw)
        except ValueError:
            raise ValueError(self.t("games.error.appid_integer"))
        if app_id not in self.config.games:
            raise ValueError(self.t("games.error.appid_missing"))
        self.config.games.remove(app_id)
        self.config_manager.save(self.config)
        print(self.t("games.removed"))
        self.show_games_summary()

    def clear_games(self) -> None:
        confirm = input(self.t("games.prompt.clear_confirm", yes=self.localization.yes_word())).strip()
        if not self.localization.is_yes(confirm):
            print(self.t("general.cancelled"))
            return
        self.config.clear_games()
        self.config_manager.save(self.config)
        print(self.t("games.cleared"))
        self.show_games_summary()

    def add_from_library(self) -> None:
        api_key = self.config.account.api_key
        steam_id = self.config.account.steam_id
        if not api_key:
            print(self.t("library.error.credentials"))
            return
        if not steam_id:
            vanity = input(self.t("library.prompt.vanity")).strip()
            if vanity:
                steam_id = self._resolve_steam_id(api_key, vanity)
                if not steam_id:
                    return
                self.config.account.steam_id = steam_id
                self.config_manager.save(self.config)
                print(self.t("library.resolved.steam_id", steam_id=steam_id))
            else:
                print(self.t("library.error.steam_id_missing"))
                return

        fetcher = SteamLibraryFetcher(api_key=api_key, steam_id=steam_id)
        print(self.t("library.fetching"))
        try:
            games = fetcher.fetch_owned_games()
        except SteamLibraryError as exc:
            print(self.t("library.error.request", reason=exc))
            return

        if not games:
            print(self.t("library.empty"))
            return

        existing_games = set(self.config.games)
        self._show_library_grid(games, existing_games)
        selection = input(self.t("library.prompt.selection")).strip()
        if not selection:
            print(self.t("general.cancelled"))
            return
        indexes = self._parse_selection(selection, len(games))
        if not indexes:
            return
        added = 0
        for idx in indexes:
            app_id = games[idx].app_id
            if self.config.add_game(app_id):
                added += 1
        if added:
            self.config_manager.save(self.config)
            self.show_games_summary()
        print(self.t("library.added", count=added))

    def start_idler(self) -> None:
        print(f"\n{self.t('start.title')}")
        username = self.config.account.username or self.t("general.not_set")
        print(self.t("start.account", username=username))
        print(self.t("start.games", count=len(self.config.games)))
        self.config.validate()
        idler = SteamIdler(self.config)
        idler.start()
        print(self.t("start.finished"))

    def reset_config(self) -> None:
        confirm = input(self.t("account.reset.confirm", yes=self.localization.yes_word())).strip()
        if not self.localization.is_yes(confirm):
            print(self.t("general.cancelled"))
            return
        self.config = self.config_manager.reset()
        self.localization.set_language(self.config.language or None)
        print(self.t("account.reset.done"))

    def change_language(self) -> None:
        options = list(SUPPORTED_LANGUAGES.items())
        print(f"\n{self.t('language.initial_title')}")
        print(self.t("language.initial_instruction"))
        for idx, (code, meta) in enumerate(options, 1):
            marker = "*" if code == self.localization.language else " "
            print(self.t("language.list.entry", index=idx, marker=marker, label=meta["label"], code=code))
        choice = input(self.t("language.prompt")).strip()
        if not choice.isdigit():
            print(self.t("language.invalid"))
            return
        index = int(choice)
        if index < 1 or index > len(options):
            print(self.t("language.invalid"))
            return
        language_code = options[index - 1][0]
        self.localization.set_language(language_code)
        self.config.language = language_code
        self.config_manager.save(self.config)
        print(self.t("language.saved", language=SUPPORTED_LANGUAGES[language_code]["label"]))

    def exit_menu(self) -> None:
        print(self.t("general.goodbye"))
        self._running = False

    def _show_library_grid(self, games, existing_app_ids: set[int] | None = None) -> None:
        existing = existing_app_ids or set()
        rows = []
        for idx, game in enumerate(games, 1):
            marker = "*" if game.app_id in existing else " "
            rows.append(f"{idx:>3}. [{marker}] {game.name} ({game.app_id})")
        column_width = min(50, max(len(row) for row in rows) + 2)
        columns = 3
        print()
        for start in range(0, len(rows), columns):
            chunk = rows[start : start + columns]
            line = "  ".join(entry.ljust(column_width) for entry in chunk)
            print(line.rstrip())
        print(f"\n{self.t('library.legend.existing')}")

    def _parse_selection(self, selection: str, max_index: int) -> list[int]:
        indexes: list[int] = []
        seen: set[int] = set()
        for part in selection.replace(" ", "").split(","):
            if not part:
                continue
            try:
                value = int(part)
            except ValueError:
                print(self.t("library.selection.invalid", value=part))
                return []
            if value < 1 or value > max_index:
                print(self.t("library.selection.out_of_range", value=value))
                return []
            zero_based = value - 1
            if zero_based not in seen:
                seen.add(zero_based)
                indexes.append(zero_based)
        if not indexes:
            print(self.t("library.selection.none"))
        return indexes

    def _resolve_steam_id(self, api_key: str, vanity: str) -> str | None:
        fetcher = SteamLibraryFetcher(api_key=api_key, steam_id="0")
        try:
            return fetcher.resolve_steam_id(vanity)
        except SteamLibraryError as exc:
            print(self.t("library.error.vanity_lookup", reason=exc))
            return None

    def _execute_pending_action(self, action: Callable[[], None]) -> bool:
        try:
            action()
            return True
        except ValueError as exc:
            print(self.t("general.error", message=exc))
            return True
        except (KeyboardInterrupt, EOFError):
            print(self.t("general.interrupted_back"))
            return False


