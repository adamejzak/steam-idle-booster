from __future__ import annotations

import getpass
from typing import Callable

from .config_manager import ConfigManager
from .config_models import MAX_SIMULTANEOUS_GAMES, AppConfig
from .library import SteamLibraryError, SteamLibraryFetcher
from .steam_idler import SteamIdler, SteamLoginError, SteamRunError


class InteractiveMenu:

    def __init__(self, config_manager: ConfigManager) -> None:
        self.config_manager = config_manager
        self.config: AppConfig = config_manager.config
        self.main_actions: dict[str, tuple[str, Callable[[], None]]] = {
            "1": ("Konfiguracja konta", self.run_account_menu),
            "2": ("Konfiguracja gier", self.run_games_menu),
            "3": ("Uruchom", self.start_idler),
            "0": ("Wyjdź", self.exit_menu),
        }
        self._running = False

    def run(self) -> None:
        self._running = True
        while self._running:
            self.print_main_menu()
            try:
                choice = input("\nWybierz opcję: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nPrzerwano. Wyłączam program.")
                break
            action = self.main_actions.get(choice, (None, None))[1]
            if not action:
                print("Nieznana opcja.")
                continue
            try:
                action()
            except (SteamLoginError, SteamRunError, ValueError) as exc:
                print(f"Błąd: {exc}")
            except (KeyboardInterrupt, EOFError):
                print("\nPrzerwano. Wyłączam program.")
                self._running = False

    def print_main_menu(self) -> None:
        header = (
            "\n╔══════════════════════════════════════╗\n"
            "║        ⏰ Steam Hour Booster         ║\n"
            "╚══════════════════════════════════════╝"
        )
        account_status = "OK" if self.config.account.username and self.config.account.password else "brak danych"
        games_status = f"{len(self.config.games)}/{MAX_SIMULTANEOUS_GAMES}"
        print(header)
        print(f"Konto: {account_status:>12}   Gry: {games_status}")
        print("\n[Menu główne]")
        for key, (label, _) in self.main_actions.items():
            print(f"  {key}. {label}")

    def run_account_menu(self) -> None:
        actions: dict[str, Callable[[], None]] = {
            "1": self.show_account_summary,
            "2": self.set_username,
            "3": self.set_password,
            "4": self.set_shared_secret,
            "5": self.set_api_key,
            "6": self.set_steam_id,
            "7": self.reset_config,
        }
        pending_action: Callable[[], None] | None = None
        while True:
            print(
                "\n╔══════════════════════╗\n"
                "║  Konto Steam – menu  ║\n"
                "╚══════════════════════╝"
            )
            print("  1. Podgląd danych")
            print("  2. Ustaw login")
            print("  3. Ustaw hasło")
            print("  4. Ustaw shared_secret (2FA)")
            print("  5. Ustaw Steam Web API Key")
            print("  6. Ustaw SteamID64")
            print("  7. Reset całej konfiguracji")
            print("  0. Powrót")
            if pending_action:
                try:
                    pending_action()
                except ValueError as exc:
                    print(f"Błąd: {exc}")
                except (KeyboardInterrupt, EOFError):
                    print("\nPrzerwano. Powrót do menu głównego.")
                    return
                pending_action = None
            try:
                choice = input("\nWybierz opcję: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nPrzerwano. Powrót do menu głównego.")
                return
            if choice == "0":
                return
            action = actions.get(choice)
            if not action:
                print("Nieznana opcja.")
                continue
            pending_action = action

    def run_games_menu(self) -> None:
        actions: dict[str, Callable[[], None]] = {
            "1": self.show_games_summary,
            "2": self.add_game,
            "3": self.remove_game,
            "4": self.clear_games,
            "5": self.add_from_library,
        }
        pending_action: Callable[[], None] | None = None
        while True:
            print(
                "\n╔══════════════════════╗\n"
                "║  Gry – konfiguracja  ║\n"
                "╚══════════════════════╝"
            )
            print("  1. Lista AppID")
            print("  2. Dodaj AppID gry")
            print("  3. Usuń AppID gry")
            print("  4. Wyczyść listę gier")
            print("  5. Dodaj z biblioteki Steam")
            print("  0. Powrót")
            if pending_action:
                try:
                    pending_action()
                except ValueError as exc:
                    print(f"Błąd: {exc}")
                except (KeyboardInterrupt, EOFError):
                    print("\nPrzerwano. Powrót do menu głównego.")
                    return
                pending_action = None
            try:
                choice = input("\nWybierz opcję: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nPrzerwano. Powrót do menu głównego.")
                return
            if choice == "0":
                return
            action = actions.get(choice)
            if not action:
                print("Nieznana opcja.")
                continue
            pending_action = action

    def show_account_summary(self) -> None:
        hidden_password = "*" * len(self.config.account.password)
        print("\n--- Konto Steam ---")
        print(f"Login: {self.config.account.username or '[nie ustawiono]'}")
        print(f"Hasło: {hidden_password or '[nie ustawiono]'}")
        print(f"Shared secret: {self.config.account.shared_secret or '[brak]'}")

    def show_games_summary(self) -> None:
        if not self.config.games:
            print("\n--- Gry ---\nBrak dodanych AppID.")
            return
        games = ", ".join(map(str, self.config.games))
        print("\n--- Gry ---")
        print(f"Liczba gier: {len(self.config.games)} / {MAX_SIMULTANEOUS_GAMES}")
        print(f"AppID-y: {games}")

    def set_username(self) -> None:
        username = input("Podaj login Steam: ").strip()
        if not username:
            raise ValueError("Login nie może być pusty.")
        self.config.account.username = username
        self.config_manager.save(self.config)
        print("Zapisano login.")

    def set_password(self) -> None:
        password = getpass.getpass("Podaj hasło Steam: ").strip()
        if not password:
            raise ValueError("Hasło nie może być puste.")
        self.config.account.password = password
        self.config_manager.save(self.config)
        print("Zapisano hasło.")

    def set_shared_secret(self) -> None:
        secret = input("Podaj shared_secret z aplikacji mobilnej (Enter aby usunąć): ").strip()
        self.config.account.shared_secret = secret or None
        self.config_manager.save(self.config)
        print("Zaktualizowano shared_secret.")

    def set_api_key(self) -> None:
        api_key = input("Podaj Steam Web API Key (https://steamcommunity.com/dev/apikey): ").strip()
        if not api_key:
            raise ValueError("API key nie może być pusty.")
        self.config.account.api_key = api_key
        self.config_manager.save(self.config)
        print("Zapisano API key.")

    def set_steam_id(self) -> None:
        steam_id = input("Podaj SteamID64 (np. 7656119...): ").strip()
        if not steam_id.isdigit():
            raise ValueError("SteamID64 musi być liczbą.")
        self.config.account.steam_id = steam_id
        self.config_manager.save(self.config)
        print("Zapisano SteamID64.")

    def add_game(self) -> None:
        if len(self.config.games) >= MAX_SIMULTANEOUS_GAMES:
            raise ValueError("Lista gier jest pełna.")
        self.show_games_summary()
        raw = input("Podaj AppID gry (liczba): ").strip()
        try:
            app_id = int(raw)
        except ValueError:
            raise ValueError("AppID musi być liczbą całkowitą.")
        if app_id <= 0:
            raise ValueError("AppID musi być dodatnie.")
        if app_id in self.config.games:
            raise ValueError("AppID już jest na liście.")
        self.config.games.append(app_id)
        self.config_manager.save(self.config)
        print("Dodano AppID.")
        self.show_games_summary()

    def remove_game(self) -> None:
        self.show_games_summary()
        if not self.config.games:
            return
        raw = input("Podaj AppID do usunięcia: ").strip()
        try:
            app_id = int(raw)
        except ValueError:
            raise ValueError("AppID musi być liczbą.")
        if app_id not in self.config.games:
            raise ValueError("AppID nie znajduje się na liście.")
        self.config.games.remove(app_id)
        self.config_manager.save(self.config)
        print("Usunięto AppID.")
        self.show_games_summary()

    def clear_games(self) -> None:
        confirm = input("Na pewno chcesz usunąć wszystkie gry? (tak/N): ").strip().lower()
        if confirm != "tak":
            print("Przerwano.")
            return
        self.config.clear_games()
        self.config_manager.save(self.config)
        print("Wyczyszczono listę gier.")
        self.show_games_summary()

    def add_from_library(self) -> None:
        api_key = self.config.account.api_key
        steam_id = self.config.account.steam_id
        if not api_key:
            print("Uzupełnij API key (https://steamcommunity.com/dev/apikey) w konfiguracji konta.")
            return
        if not steam_id:
            vanity = input("Podaj nazwę profilu Steam (vanity URL), aby pobrać SteamID64: ").strip()
            if vanity:
                steam_id = self._resolve_steam_id(api_key, vanity)
                if not steam_id:
                    return
                self.config.account.steam_id = steam_id
                self.config_manager.save(self.config)
                print(f"Ustalono SteamID64: {steam_id}")
            else:
                print("Najpierw ustaw SteamID64.")
                return

        fetcher = SteamLibraryFetcher(api_key=api_key, steam_id=steam_id)
        print("Pobieram bibliotekę gier ze Steam...")
        try:
            games = fetcher.fetch_owned_games()
        except SteamLibraryError as exc:
            print(f"Nie udało się pobrać biblioteki: {exc}")
            return

        if not games:
            print("Biblioteka jest pusta.")
            return

        existing_games = set(self.config.games)
        self._show_library_grid(games, existing_games)
        selection = input("Podaj numery gier do dodania (np. 1,3,5) lub Enter aby anulować: ").strip()
        if not selection:
            print("Przerwano.")
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
        print(f"Dodano z biblioteki {added} gier.")

    def start_idler(self) -> None:
        print("\n=== ⏰ Start programu Steam ===")
        print(f"Konto: {self.config.account.username or '[nie ustawiono]'}")
        print(f"Gry do uruchomienia: {len(self.config.games)}")
        self.config.validate()
        idler = SteamIdler(self.config)
        idler.start()
        print("⏰ Program zatrzymany.")

    def reset_config(self) -> None:
        confirm = input("Reset usunie login/hasło. Kontynuować? (tak/N): ").strip().lower()
        if confirm != "tak":
            print("Przerwano.")
            return
        self.config = self.config_manager.reset()
        print("Przywrócono domyślną konfigurację.")

    def exit_menu(self) -> None:
        print("Do zobaczenia!")
        self._running = False

    def _show_library_grid(self, games, existing_app_ids: set[int] | None = None):
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
        print("\n[*] Gra oznaczona gwiazdką jest już dodana do listy gier.")

    def _parse_selection(self, selection: str, max_index: int) -> list[int]:
        indexes: list[int] = []
        seen: set[int] = set()
        for part in selection.replace(" ", "").split(","):
            if not part:
                continue
            try:
                value = int(part)
            except ValueError:
                print(f"Nieprawidłowy numer: {part}")
                return []
            if value < 1 or value > max_index:
                print(f"Numer poza zakresem: {value}")
                return []
            zero_based = value - 1
            if zero_based not in seen:
                seen.add(zero_based)
                indexes.append(zero_based)
        if not indexes:
            print("Nie wybrano gier.")
        return indexes

    def _resolve_steam_id(self, api_key: str, vanity: str) -> str | None:
        fetcher = SteamLibraryFetcher(api_key=api_key, steam_id="0")
        try:
            return fetcher.resolve_steam_id(vanity)
        except SteamLibraryError as exc:
            print(f"Nie udało się ustalić SteamID64: {exc}")
            return None


