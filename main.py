from __future__ import annotations

from steam_hour.config_manager import ConfigManager
from steam_hour.menu import InteractiveMenu


def main() -> None:
    manager = ConfigManager("config.json")
    menu = InteractiveMenu(manager)
    menu.run()


if __name__ == "__main__":
    main()


