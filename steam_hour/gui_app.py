from __future__ import annotations

import queue
import sys
import threading
from pathlib import Path
from typing import Optional, Tuple

try:
    import qdarktheme
except ImportError:
    qdarktheme = None
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, QUrl
from PySide6.QtGui import QColor, QCloseEvent, QIcon, QPalette, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .app_directory import SteamAppDirectory, format_app_display
from .config_manager import ConfigManager
from .config_models import AppConfig, ConfigValidationError, MAX_SIMULTANEOUS_GAMES
from .library import LIBRARY_ERROR_UNAUTHORIZED, SteamLibraryError, SteamLibraryFetcher
from .localization import DEFAULT_LANGUAGE, Localization, SUPPORTED_LANGUAGES
from .steam_idler import PromptCancelled, SteamIdler
from .version import CURRENT_VERSION, REPOSITORY_URL, fetch_latest_version, is_newer_version


def _get_icon_path() -> Path | None:
    """Returns the path to the application icon if it exists."""
    base_path = Path(getattr(sys, '_MEIPASS', Path(__file__).parent.parent))
    
    icon_path = base_path / "icon.ico"
    if icon_path.exists():
        return icon_path
    
    icon_path = Path("icon.ico")
    if icon_path.exists():
        return icon_path
    
    icon_path = Path(__file__).parent.parent / "icon.ico"
    if icon_path.exists():
        return icon_path
    
    return None


_DARK_STYLE_SHEET = """
QToolTip {
    color: #f5f5f5;
    background-color: #2b2b2b;
    border: 1px solid #3d3d3d;
    padding: 4px;
    border-radius: 4px;
}
QPushButton {
    background-color: #30343d;
    border: 1px solid #3f434c;
    padding: 6px 12px;
    border-radius: 4px;
}
QPushButton:disabled {
    background-color: #25262b;
    border-color: #2d2f35;
    color: #6f6f6f;
}
QPushButton:hover {
    background-color: #3b404a;
}
QPushButton:pressed {
    background-color: #2a2d35;
}
QLineEdit,
QPlainTextEdit,
QTextEdit,
QComboBox,
QListView,
QTreeView,
QSpinBox,
QDoubleSpinBox {
    background-color: #1f1f1f;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    selection-background-color: #3b7dd8;
    selection-color: #f5f5f5;
}
QTabWidget::pane {
    border: 1px solid #3a3a3a;
    padding: 6px;
}
QTabBar::tab {
    background: #2a2a2a;
    border: 1px solid #3a3a3a;
    border-bottom-color: #3a3a3a;
    padding: 6px 12px;
}
QTabBar::tab:selected {
    background: #343434;
}
QScrollBar:vertical {
    border: none;
    background: #1b1b1b;
    width: 12px;
    margin: 16px 0 16px 0;
}
QScrollBar::handle:vertical {
    background: #3d3d3d;
    border-radius: 6px;
}
QScrollBar::handle:vertical:hover {
    background: #4a4a4a;
}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0px;
}
"""

class GuiIdlerUI(QObject):
    log_emitted = Signal(str)
    prompt_requested = Signal(str, bool)
    clock_updated = Signal(str, bool)
    stop_prompted = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._prompt_queue: queue.Queue[Tuple[str, bool]] | None = None
        self._prompt_lock = threading.Lock()
        self._stop_event: threading.Event | None = None

    def log(self, message: str) -> None:
        self.log_emitted.emit(message)

    def prompt_text(self, prompt: str) -> str:
        return self._request_input(prompt, secret=False)

    def prompt_secret(self, prompt: str) -> str:
        return self._request_input(prompt, secret=True)

    def start_stop_listener(self, prompt: str, stop_event: threading.Event):
        self._stop_event = stop_event
        self.stop_prompted.emit(prompt)
        return None

    def update_clock(self, message: str, *, final: bool = False) -> None:
        self.clock_updated.emit(message, final)

    def trigger_stop(self) -> None:
        if self._stop_event and not self._stop_event.is_set():
            self._stop_event.set()
        with self._prompt_lock:
            if self._prompt_queue and self._prompt_queue.empty():
                self._prompt_queue.put(("", False))
                self._prompt_queue = None

    def submit_prompt_response(self, value: str, *, cancelled: bool = False) -> None:
        with self._prompt_lock:
            queue_ref = self._prompt_queue
        if queue_ref is not None:
            queue_ref.put((value, cancelled))

    def _request_input(self, prompt: str, *, secret: bool) -> str:
        response_queue: queue.Queue[Tuple[str, bool]] = queue.Queue(maxsize=1)
        with self._prompt_lock:
            self._prompt_queue = response_queue
        self.prompt_requested.emit(prompt, secret)
        try:
            value, cancelled = response_queue.get()
            if cancelled:
                raise PromptCancelled()
            return value
        finally:
            with self._prompt_lock:
                self._prompt_queue = None


class IdlerWorker(QThread):
    finished_success = Signal()
    failed = Signal(str)

    def __init__(self, config: AppConfig, localization: Localization, ui: GuiIdlerUI) -> None:
        super().__init__()
        self.config = config
        self.localization = localization
        self.ui = ui

    def run(self) -> None:
        try:
            idler = SteamIdler(self.config, localization=self.localization, ui=self.ui, keep_session=True)
            idler.start()
            self.finished_success.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class LibraryFetchWorker(QThread):
    fetched = Signal(object)
    failed = Signal(str)
    resolved = Signal(str)

    def __init__(self, api_key: str, steam_id: Optional[str], vanity: Optional[str]) -> None:
        super().__init__()
        self.api_key = api_key
        self.steam_id = steam_id
        self.vanity = vanity

    def run(self) -> None:
        try:
            steam_id = self.steam_id
            if (not steam_id) and self.vanity:
                resolver = SteamLibraryFetcher(api_key=self.api_key, steam_id="0")
                steam_id = resolver.resolve_steam_id(self.vanity)
                self.resolved.emit(steam_id)
            if not steam_id:
                self.failed.emit("SteamID64 jest wymagane.")
                return
            fetcher = SteamLibraryFetcher(api_key=self.api_key, steam_id=steam_id)
            games = fetcher.fetch_owned_games()
            payload = [(entry.app_id, entry.name) for entry in games]
            self.fetched.emit(payload)
        except SteamLibraryError as exc:
            self.failed.emit(str(exc))


class VersionCheckWorker(QThread):
    update_available = Signal(str)

    def run(self) -> None:
        latest = fetch_latest_version()
        if self.isInterruptionRequested():
            return
        if latest and is_newer_version(latest):
            self.update_available.emit(latest)


class LibrarySelectionDialog(QDialog):
    def __init__(
        self,
        games: list[tuple[int, str]],
        existing: set[int],
        localization: Localization,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.localization = localization
        self.setWindowTitle(self.t("gui.dialog.library_title"))
        self.resize(600, 500)
        layout = QVBoxLayout(self)

        label = QLabel(self.t("gui.library.instructions"))
        label.setWordWrap(True)
        layout.addWidget(label)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.MultiSelection)
        for app_id, name in games:
            label = format_app_display(app_id, name)
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, app_id)
            if app_id in existing:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                item.setText(f"[*] {label}")
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_app_ids(self) -> list[int]:
        return [item.data(Qt.UserRole) for item in self.list_widget.selectedItems()]

    def t(self, key: str, **kwargs: object) -> str:
        return self.localization.translate(key, **kwargs)


class PromptDialog(QDialog):
    def __init__(
        self,
        localization: Localization,
        prompt: str,
        *,
        secret: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.localization = localization
        self.setWindowTitle(self.t("gui.dialog.info"))
        layout = QVBoxLayout(self)

        self.prompt_label = QLabel(prompt)
        self.prompt_label.setWordWrap(True)
        layout.addWidget(self.prompt_label)

        self.input_field = QLineEdit()
        if secret:
            self.input_field.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.input_field)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        cancel_button = buttons.button(QDialogButtonBox.Cancel)
        ok_button.setText(self.t("gui.buttons.ok"))
        cancel_button.setText(self.t("gui.buttons.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def t(self, key: str, **kwargs: object) -> str:
        return self.localization.translate(key, **kwargs)

    @staticmethod
    def get_text(
        localization: Localization,
        prompt: str,
        *,
        secret: bool,
        parent: QWidget | None,
    ) -> tuple[bool, str]:
        dialog = PromptDialog(localization, prompt, secret=secret, parent=parent)
        result = dialog.exec() == QDialog.Accepted
        return result, dialog.input_field.text()


class InitialSetupDialog(QDialog):
    def __init__(self, localization: Localization) -> None:
        super().__init__()
        self.localization = Localization(localization.language)
        self._selected_language = self.localization.language
        self._username: str = ""
        self._password: str = ""

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_language_page())
        self.stack.addWidget(self._build_credentials_page())

        layout = QVBoxLayout(self)
        layout.addWidget(self.stack)

        buttons = QHBoxLayout()
        self.back_button = QPushButton()
        self.back_button.setEnabled(False)
        self.next_button = QPushButton()
        self.cancel_button = QPushButton()
        self.back_button.clicked.connect(self._handle_back)
        self.next_button.clicked.connect(self._handle_next)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.back_button)
        buttons.addStretch()
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.next_button)
        layout.addLayout(buttons)

        self._update_language_texts()
        self._select_initial_language()

    def t(self, key: str, **kwargs: object) -> str:
        return self.localization.translate(key, **kwargs)

    def _build_language_page(self) -> QWidget:
        page = QWidget()
        vbox = QVBoxLayout(page)
        self.language_title = QLabel()
        self.language_title.setWordWrap(True)
        self.language_hint = QLabel()
        self.language_hint.setWordWrap(True)
        self.language_list = QListWidget()
        for code, meta in SUPPORTED_LANGUAGES.items():
            item = QListWidgetItem(f"{meta['label']} ({code})")
            item.setData(Qt.UserRole, code)
            self.language_list.addItem(item)
        self.language_list.currentItemChanged.connect(self._handle_language_change)
        vbox.addWidget(self.language_title)
        vbox.addWidget(self.language_hint)
        vbox.addWidget(self.language_list)
        return page

    def _build_credentials_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.credentials_hint = QLabel()
        self.credentials_hint.setWordWrap(True)
        form.addRow(self.credentials_hint)
        self.username_field = QLineEdit()
        self.password_field = QLineEdit()
        self.password_field.setEchoMode(QLineEdit.Password)
        self.username_label = QLabel()
        self.password_label = QLabel()
        form.addRow(self.username_label, self.username_field)
        form.addRow(self.password_label, self.password_field)
        self.credentials_note = QLabel()
        self.credentials_note.setWordWrap(True)
        form.addRow(self.credentials_note)
        return page

    def _select_initial_language(self) -> None:
        for row in range(self.language_list.count()):
            item = self.language_list.item(row)
            if item.data(Qt.UserRole) == self._selected_language:
                self.language_list.setCurrentRow(row)
                return
        self.language_list.setCurrentRow(0)

    def _handle_language_change(self, current: QListWidgetItem | None, _: QListWidgetItem | None) -> None:
        if not current:
            return
        code = current.data(Qt.UserRole)
        if not code:
            return
        self._selected_language = code
        self.localization.set_language(code)
        self._update_language_texts()

    def _handle_next(self) -> None:
        if self.stack.currentIndex() == 0:
            if not self._selected_language:
                QMessageBox.warning(self, self.t("gui.dialog.error"), self.t("language.invalid"))
                return
            self.stack.setCurrentIndex(1)
            self.back_button.setEnabled(True)
            self.next_button.setText(self.t("gui.initial.finish"))
            return

        username = self.username_field.text().strip()
        password = self.password_field.text()
        if not username:
            QMessageBox.warning(self, self.t("gui.dialog.error"), self.t("gui.initial.username_required"))
            return
        if not password:
            QMessageBox.warning(self, self.t("gui.dialog.error"), self.t("gui.initial.password_required"))
            return
        self._username = username
        self._password = password
        self.accept()

    def _handle_back(self) -> None:
        if self.stack.currentIndex() == 1:
            self.stack.setCurrentIndex(0)
            self.back_button.setEnabled(False)
            self.next_button.setText(self.t("gui.initial.next"))

    def _update_language_texts(self) -> None:
        self.setWindowTitle(self.t("gui.window.title"))
        self.language_title.setText(self.t("language.initial_title"))
        self.language_hint.setText(self.t("gui.initial.language_hint"))
        self.credentials_hint.setText(self.t("gui.initial.credentials_hint"))
        self.username_label.setText(self.t("gui.initial.username"))
        self.password_label.setText(self.t("gui.initial.password"))
        self.username_field.setPlaceholderText(self.t("gui.initial.username"))
        self.password_field.setPlaceholderText(self.t("gui.initial.password"))
        self.credentials_note.setText(self.t("language.initial_instruction"))
        self.back_button.setText(self.t("gui.initial.back"))
        self.next_button.setText(
            self.t("gui.initial.finish") if self.stack.currentIndex() == 1 else self.t("gui.initial.next")
        )
        self.cancel_button.setText(self.t("gui.initial.cancel"))

    def result_values(self) -> tuple[str, str, str]:
        return self._selected_language or DEFAULT_LANGUAGE, self._username, self._password


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Steam Idle Booster – GUI")
        self.resize(960, 640)
        
        icon_path = _get_icon_path()
        if icon_path:
            self.setWindowIcon(QIcon(str(icon_path)))
        self.config_manager = ConfigManager("config.json")
        self.config = self.config_manager.config
        self.localization = Localization(self.config.language or None)
        self._running = False
        self._library_busy = False
        self.app_directory = SteamAppDirectory()
        self._status_key = "gui.status.idle"
        self.current_version = CURRENT_VERSION

        self.idler_ui: GuiIdlerUI | None = None
        self.idler_worker: IdlerWorker | None = None
        self.library_worker: LibraryFetchWorker | None = None
        self.version_worker: VersionCheckWorker | None = None

        if self._needs_initial_setup():
            if not self._run_initial_setup():
                QTimer.singleShot(0, QApplication.instance().quit)
                return

        self._build_ui()
        self._populate_account_fields()
        self._update_status_summary()
        self._refresh_games_list()
        self._apply_translations()
        self._start_version_check()

    def t(self, key: str, **kwargs: object) -> str:
        return self.localization.translate(key, **kwargs)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(16, 16, 16, 16)
        outer_layout.setSpacing(12)

        root_layout = QHBoxLayout()
        root_layout.setSpacing(16)

        main_panel = self._build_main_panel()
        settings_panel = self._build_settings_panel()

        root_layout.addWidget(main_panel, 2)
        root_layout.addWidget(settings_panel, 1)
        outer_layout.addLayout(root_layout, 1)

        self.footer_label = QLabel()
        self.footer_label.setAlignment(Qt.AlignRight)
        self.footer_label.setObjectName("footerLabel")
        outer_layout.addWidget(self.footer_label)
        self._apply_app_styles()

    def _build_main_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("mainPanel")
        layout = QVBoxLayout(panel)
        layout.setSpacing(12)
        layout.setContentsMargins(0, 0, 0, 0)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)

        account_card, acc_title, acc_value, acc_detail = self._create_status_card()
        games_card, games_title, games_value, games_detail = self._create_status_card()
        timer_card, timer_title, timer_value, timer_detail = self._create_status_card()
        stats_row.addWidget(account_card, 1)
        stats_row.addWidget(games_card, 1)
        stats_row.addWidget(timer_card, 1)

        self.account_card_title = acc_title
        self.account_card_value = acc_value
        self.account_card_detail = acc_detail
        self.games_card_title = games_title
        self.games_card_value = games_value
        self.games_card_detail = games_detail
        self.timer_card_title = timer_title
        self.timer_card_value = timer_value
        self.timer_card_detail = timer_detail
        self.timer_card_value.setText("00:00:00")

        layout.addLayout(stats_row)

        self.status_label = QLabel()
        layout.addWidget(self.status_label)

        controls = QHBoxLayout()
        self.start_button = QPushButton()
        self.start_button.clicked.connect(self._handle_booster_button)
        controls.addWidget(self.start_button)
        controls.addStretch()
        layout.addLayout(controls)

        self.stop_hint_label = QLabel("")
        layout.addWidget(self.stop_hint_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, 1)

        return panel

    def _build_settings_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("settingsPanel")
        layout = QVBoxLayout(panel)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        self.settings_header = QLabel()
        self.settings_header.setObjectName("panelHeader")
        layout.addWidget(self.settings_header)

        self.settings_tabs = QTabWidget()
        self.account_tab = self._build_account_tab()
        self.games_tab = self._build_games_tab()
        self.settings_tabs.addTab(self.account_tab, "")
        self.settings_tabs.addTab(self.games_tab, "")
        layout.addWidget(self.settings_tabs, 1)

        return panel

    def _create_status_card(self) -> tuple[QFrame, QLabel, QLabel, QLabel]:
        card = QFrame()
        card.setObjectName("statusCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        title_label = QLabel()
        title_label.setObjectName("cardTitle")
        value_label = QLabel("--")
        value_label.setObjectName("cardValue")
        detail_label = QLabel("")
        detail_label.setObjectName("cardDetail")
        card_layout.addWidget(title_label)
        card_layout.addWidget(value_label)
        card_layout.addWidget(detail_label)
        card_layout.addStretch()
        return card, title_label, value_label, detail_label

    def _apply_app_styles(self) -> None:
        self.setStyleSheet(
            """
            QFrame#mainPanel {
                background-color: rgba(255, 255, 255, 0.02);
                border-radius: 16px;
                padding: 16px;
            }
            QFrame#settingsPanel {
                background-color: rgba(255, 255, 255, 0.03);
                border-radius: 16px;
            }
            QFrame#statusCard {
                background-color: rgba(255, 255, 255, 0.04);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 16px;
            }
            QLabel#cardTitle {
                font-size: 12px;
                letter-spacing: 1px;
                color: #9aa0a6;
            }
            QLabel#cardValue {
                font-size: 24px;
                font-weight: 600;
            }
            QLabel#cardDetail {
                font-size: 12px;
                color: #9aa0a6;
            }
            QLabel#panelHeader {
                font-size: 18px;
                font-weight: 600;
            }
            QLabel#footerLabel {
                font-size: 11px;
                color: #9aa0a6;
            }
            """
        )

    def _apply_translations(self) -> None:
        if not hasattr(self, "status_label"):
            return
        self.setWindowTitle(self.t("gui.window.title"))
        self.settings_header.setText(self.t("gui.panel.settings"))
        self.settings_tabs.setTabText(0, self.t("gui.tab.account"))
        self.settings_tabs.setTabText(1, self.t("gui.tab.games"))
        self.account_card_title.setText(self.t("gui.card.account").upper())
        self.games_card_title.setText(self.t("gui.card.games").upper())
        self.timer_card_title.setText(self.t("gui.card.session").upper())
        self.timer_card_detail.setText(self.t("gui.card.session_detail"))
        self.start_button.setText(self.t("gui.buttons.start"))
        self.log_view.setPlaceholderText(self.t("gui.log.placeholder"))
        self.required_group_box.setTitle(self.t("gui.account.required_group"))
        self.optional_group_box.setTitle(self.t("gui.account.optional_group"))
        self.username_label.setText(self.t("gui.account.username"))
        self.password_label.setText(self.t("gui.account.password"))
        self.language_label.setText(self.t("gui.account.language"))
        self.username_input.setPlaceholderText(self.t("gui.account.username"))
        self.password_input.setPlaceholderText(self.t("gui.account.password"))
        self.api_key_label.setText(self.t("gui.account.api_key"))
        self.steam_id_label.setText(self.t("gui.account.steam_id"))
        self.shared_secret_label.setText(self.t("gui.account.shared_secret"))
        self.api_key_input.setPlaceholderText(self.t("gui.account.api_key"))
        self.steam_id_input.setPlaceholderText(self.t("gui.account.steam_id"))
        self.secret_input.setPlaceholderText(self.t("gui.account.shared_secret"))
        self.optional_hint_label.setText(self.t("gui.account.optional_hint"))
        self.save_button.setText(self.t("gui.account.save"))
        self.reset_button.setText(self.t("gui.account.reset"))
        self.games_header.setText(self.t("gui.games.header", max=MAX_SIMULTANEOUS_GAMES))
        self.game_input.setPlaceholderText(self.t("gui.games.placeholder"))
        self.game_add_btn.setText(self.t("gui.games.add"))
        self.game_remove_btn.setText(self.t("gui.games.remove"))
        self.game_clear_btn.setText(self.t("gui.games.clear"))
        self.game_library_btn.setText(self.t("gui.games.library"))
        self.games_hint.setText(self.t("gui.games.hint"))
        self._set_status(self._status_key)
        self._update_controls()
        if hasattr(self, "footer_label"):
            self.footer_label.setText(
                self.t("gui.footer.info", author="ajzak", version=self.current_version)
            )

    def _set_status(self, key: str) -> None:
        self._status_key = key
        if hasattr(self, "status_label"):
            self.status_label.setText(self.t(key))

    def _handle_booster_button(self) -> None:
        if self._running:
            self._stop_idler()
        else:
            self._start_idler()

    def _update_controls(self) -> None:
        busy = self._running
        self.start_button.setText(self.t("gui.buttons.stop") if self._running else self.t("gui.buttons.start"))
        if hasattr(self, "language_combo_widget"):
            self.language_combo_widget.setDisabled(busy)
        self._apply_tab_states()

    def _build_language_combo(self) -> QWidget:
        combo = QComboBox()
        for code, meta in SUPPORTED_LANGUAGES.items():
            combo.addItem(meta["label"], code)
            if code == self.localization.language:
                combo.setCurrentIndex(combo.count() - 1)
        combo.currentIndexChanged.connect(lambda _: self._change_language(combo.currentData()))
        self.language_combo_widget = combo
        return combo

    def _build_account_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        self.required_group_box = QGroupBox()
        required_form = QFormLayout(self.required_group_box)
        required_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.username_input = QLineEdit(self.config.account.username)
        self.password_input = QLineEdit(self.config.account.password)
        self.password_input.setEchoMode(QLineEdit.Password)
        self.username_label = QLabel()
        self.password_label = QLabel()
        self.language_label = QLabel()

        required_form.addRow(self.username_label, self.username_input)
        required_form.addRow(self.password_label, self.password_input)
        required_form.addRow(self.language_label, self._build_language_combo())

        layout.addWidget(self.required_group_box)

        self.optional_group_box = QGroupBox()
        optional_form = QFormLayout(self.optional_group_box)
        optional_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.api_key_input = QLineEdit(self.config.account.api_key or "")
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.steam_id_input = QLineEdit(self.config.account.steam_id or "")
        self.secret_input = QLineEdit(self.config.account.shared_secret or "")
        self.secret_input.setEchoMode(QLineEdit.Password)

        self.api_key_label = QLabel()
        self.steam_id_label = QLabel()
        self.shared_secret_label = QLabel()
        self.optional_hint_label = QLabel()
        self.optional_hint_label.setWordWrap(True)

        optional_form.addRow(self.api_key_label, self.api_key_input)
        optional_form.addRow(self.steam_id_label, self.steam_id_input)
        optional_form.addRow(self.shared_secret_label, self.secret_input)
        optional_form.addRow(self.optional_hint_label)

        layout.addWidget(self.optional_group_box)

        buttons = QHBoxLayout()
        self.save_button = QPushButton()
        self.reset_button = QPushButton()
        self.save_button.clicked.connect(self._save_account)
        self.reset_button.clicked.connect(self._reset_config)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.reset_button)
        buttons.addStretch()
        layout.addLayout(buttons)

        return tab

    def _build_games_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        self.games_header = QLabel()
        self.games_header.setObjectName("panelHeader")
        layout.addWidget(self.games_header)

        self.games_list = QListWidget()
        self.games_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.games_list, 1)

        app_id_row = QHBoxLayout()
        self.game_input = QLineEdit()
        self.game_add_btn = QPushButton()
        self.game_add_btn.clicked.connect(self._add_game)
        app_id_row.addWidget(self.game_input, 1)
        app_id_row.addWidget(self.game_add_btn)
        layout.addLayout(app_id_row)

        actions_row = QHBoxLayout()
        self.game_remove_btn = QPushButton()
        self.game_clear_btn = QPushButton()
        self.game_library_btn = QPushButton()
        self.game_library_btn.setMinimumWidth(170)
        self.game_remove_btn.clicked.connect(self._remove_selected_games)
        self.game_clear_btn.clicked.connect(self._clear_games)
        self.game_library_btn.clicked.connect(self._handle_add_from_library)
        actions_row.addWidget(self.game_remove_btn)
        actions_row.addWidget(self.game_clear_btn)
        actions_row.addWidget(self.game_library_btn)
        layout.addLayout(actions_row)

        self.games_hint = QLabel()
        self.games_hint.setWordWrap(True)
        layout.addWidget(self.games_hint)

        return tab

    def _change_language(self, language_code: str) -> None:
        if not language_code or language_code == self.localization.language:
            return
        self.localization.set_language(language_code)
        self.config.language = language_code
        self.config_manager.save(self.config)
        self._update_status_summary()
        self._apply_translations()

    def _save_account(self) -> None:
        self._update_config_from_fields()
        self.config_manager.save(self.config)
        self._update_status_summary()
        self._show_message(self.t("gui.account.saved"))

    def _reset_config(self) -> None:
        answer = QMessageBox.question(
            self,
            self.t("gui.dialog.reset_config"),
            self.t("account.reset.confirm", yes=self.localization.yes_word()),
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.config = self.config_manager.reset()
        self.localization.set_language(self.config.language or None)
        self._populate_account_fields()
        self._refresh_games_list()
        self._update_status_summary()
        self._apply_translations()

    def _populate_account_fields(self) -> None:
        self.username_input.setText(self.config.account.username)
        self.password_input.setText(self.config.account.password)
        self.secret_input.setText(self.config.account.shared_secret or "")
        self.api_key_input.setText(self.config.account.api_key or "")
        self.steam_id_input.setText(self.config.account.steam_id or "")
        if hasattr(self, "language_combo_widget"):
            combo = self.language_combo_widget
            index = combo.findData(self.localization.language)
            if index >= 0:
                combo.setCurrentIndex(index)

    def _update_config_from_fields(self) -> None:
        self.config.account.username = self.username_input.text().strip()
        self.config.account.password = self.password_input.text()
        secret = self.secret_input.text().strip()
        self.config.account.shared_secret = secret or None
        api_key = self.api_key_input.text().strip()
        self.config.account.api_key = api_key or None
        steam_id = self.steam_id_input.text().strip()
        self.config.account.steam_id = steam_id or None

    def _add_game(self) -> None:
        raw = self.game_input.text().strip()
        if not raw:
            self._show_error(self.t("gui.games.add_required"))
            return
        try:
            app_id = int(raw)
        except ValueError:
            self._show_error(self.t("games.error.appid_integer"))
            return
        if app_id <= 0:
            self._show_error(self.t("games.error.appid_positive"))
            return
        if not self.config.add_game(app_id):
            if app_id in self.config.games:
                message = self.t("games.error.appid_exists")
            elif len(self.config.games) >= MAX_SIMULTANEOUS_GAMES:
                message = self.t("games.error.full")
            else:
                message = self.t("games.error.appid_positive")
            self._show_error(message)
            return
        self.config_manager.save(self.config)
        self._refresh_games_list()
        self._update_status_summary()
        self.game_input.clear()

    def _remove_selected_games(self) -> None:
        items = self.games_list.selectedItems()
        if not items:
            self._show_error(self.t("gui.games.select_remove"))
            return
        removed = 0
        for item in items:
            app_id = item.data(Qt.UserRole)
            if self.config.remove_game(app_id):
                removed += 1
        if removed:
            self.config_manager.save(self.config)
            self._refresh_games_list()
            self._update_status_summary()

    def _clear_games(self) -> None:
        if not self.config.games:
            return
        answer = QMessageBox.question(
            self,
            self.t("gui.dialog.clear_games"),
            self.t("games.prompt.clear_confirm", yes=self.localization.yes_word()),
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.config.clear_games()
        self.config_manager.save(self.config)
        self._refresh_games_list()
        self._update_status_summary()

    def _handle_add_from_library(self) -> None:
        self._update_config_from_fields()
        api_key = self.config.account.api_key
        steam_id = self.config.account.steam_id
        if not api_key:
            self._show_error(self.t("library.error.credentials"), "gui.dialog.library_title")
            return
        vanity: Optional[str] = None
        if not steam_id:
            vanity, ok = QInputDialog.getText(
                self,
                self.t("gui.dialog.library_title"),
                self.t("library.prompt.vanity"),
            )
            if not ok or not vanity.strip():
                return
            vanity = vanity.strip()
        self._set_library_busy(True)
        self.library_worker = LibraryFetchWorker(api_key=api_key, steam_id=steam_id, vanity=vanity)
        self.library_worker.fetched.connect(self._on_library_fetched)
        self.library_worker.failed.connect(self._on_library_failed)
        self.library_worker.resolved.connect(self._on_library_resolved)
        self.library_worker.finished.connect(lambda: self._set_library_busy(False))
        self.library_worker.start()

    def _on_library_resolved(self, steam_id: str) -> None:
        self.config.account.steam_id = steam_id
        self.steam_id_input.setText(steam_id)
        self.config_manager.save(self.config)
        self._show_message(self.t("library.resolved.steam_id", steam_id=steam_id), "gui.dialog.library_title")

    def _on_library_fetched(self, payload: list[tuple[int, str]]) -> None:
        if not payload:
            self._show_message(self.t("library.empty"), "gui.dialog.library_title")
            return
        for app_id, name in payload:
            self.app_directory.remember_name(app_id, name)
        self._refresh_games_list()
        existing = set(self.config.games)
        name_lookup = {app_id: name for app_id, name in payload}
        dialog = LibrarySelectionDialog(payload, existing, self.localization, self)
        if dialog.exec() != QDialog.Accepted:
            return
        selected = dialog.selected_app_ids()
        if not selected:
            self._show_message(self.t("library.selection.none"), "gui.dialog.library_title")
            return
        added = 0
        for app_id in selected:
            friendly_name = name_lookup.get(app_id)
            if friendly_name:
                self.app_directory.remember_name(app_id, friendly_name)
            if self.config.add_game(app_id):
                added += 1
        if added:
            self.config_manager.save(self.config)
            self._refresh_games_list()
            self._update_status_summary()
        self._show_message(self.t("library.added", count=added), "gui.dialog.library_title")

    def _on_library_failed(self, reason: str) -> None:
        if reason == LIBRARY_ERROR_UNAUTHORIZED:
            message = self.t("library.error.unauthorized")
        else:
            message = self.t("library.error.request", reason=reason)
        self._show_error(message, "gui.dialog.library_title")

    def _set_library_busy(self, busy: bool) -> None:
        self._library_busy = busy
        self._apply_tab_states()
        if busy:
            self.statusBar().showMessage(self.t("library.fetching"))
        else:
            self.statusBar().clearMessage()

    def _refresh_games_list(self) -> None:
        self.games_list.clear()
        for app_id in self.config.games:
            item = QListWidgetItem(self._format_game_label(app_id))
            item.setData(Qt.UserRole, app_id)
            self.games_list.addItem(item)

    def _update_status_summary(self) -> None:
        if not hasattr(self, "account_card_value"):
            return
        account_ok = bool(self.config.account.username and self.config.account.password)
        account_status = (
            self.t("menu.status.account_ok") if account_ok else self.t("menu.status.account_missing")
        )
        username = self.config.account.username or self.t("general.not_set")
        self.account_card_value.setText(username if account_ok else self.t("general.not_set"))
        self.account_card_detail.setText("" if account_ok else account_status)

        games_count = len(self.config.games)
        self.games_card_value.setText(
            self.t("gui.card.games_value", count=games_count, max=MAX_SIMULTANEOUS_GAMES)
        )
        self.games_card_detail.setText("")

    def _start_idler(self) -> None:
        if self.idler_worker and self.idler_worker.isRunning():
            self._show_error(self.t("gui.run.already_running"))
            return
        self._update_config_from_fields()
        try:
            self.config.validate()
        except ConfigValidationError as exc:
            message = self.localization.translate(exc.code)
            self._show_error(message)
            return
        self.config_manager.save(self.config)
        self._set_running_state(True)
        self.log_view.clear()
        self.idler_ui = GuiIdlerUI()
        self.idler_ui.log_emitted.connect(self._append_log)
        self.idler_ui.prompt_requested.connect(self._on_prompt_requested)
        self.idler_ui.clock_updated.connect(self._on_clock_updated)
        self.idler_ui.stop_prompted.connect(self._on_stop_prompted)

        self.idler_worker = IdlerWorker(self.config, self.localization, self.idler_ui)
        self.idler_worker.finished_success.connect(self._on_idler_finished)
        self.idler_worker.failed.connect(self._on_idler_failed)
        self.idler_worker.finished.connect(self._cleanup_idler_worker)
        self.idler_worker.start()
        self._set_status("gui.status.running")

    def _stop_idler(self) -> None:
        if self.idler_ui:
            self.idler_ui.trigger_stop()
        self._set_status("gui.status.stopping")

    def _on_idler_finished(self) -> None:
        self._append_log(self.t("start.finished"))
        self._set_status("gui.status.idle")
        self._set_running_state(False)

    def _on_idler_failed(self, reason: str) -> None:
        self._append_log(self.t("general.error", message=reason))
        self._show_error(self.t("general.error", message=reason))
        self._set_status("gui.status.error")
        self._set_running_state(False)

    def _cleanup_idler_worker(self) -> None:
        self.stop_hint_label.setText("")
        if self.idler_worker:
            self.idler_worker.deleteLater()
            self.idler_worker = None
        self.idler_ui = None

    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _on_prompt_requested(self, prompt: str, secret: bool) -> None:
        accepted, text = PromptDialog.get_text(
            self.localization,
            prompt,
            secret=secret,
            parent=self,
        )
        if self.idler_ui:
            self.idler_ui.submit_prompt_response(text.strip(), cancelled=not accepted)

    def _on_clock_updated(self, message: str, final: bool) -> None:
        self.timer_card_value.setText(message)

    def _on_stop_prompted(self, prompt: str) -> None:
        self.stop_hint_label.setText(f"{prompt} {self.t('gui.stop.hint_suffix')}")

    def _set_running_state(self, running: bool) -> None:
        self._running = running
        self._update_controls()
        if not running:
            self.timer_card_value.setText("00:00:00")
            self.stop_hint_label.setText("")

    def _apply_tab_states(self) -> None:
        busy = self._running
        self.account_tab.setDisabled(busy)
        self.games_tab.setDisabled(busy or self._library_busy)

    def _start_version_check(self) -> None:
        if self.version_worker:
            return
        self.version_worker = VersionCheckWorker()
        self.version_worker.update_available.connect(self._on_update_available)
        self.version_worker.finished.connect(self._clear_version_worker)
        self.version_worker.start()

    def _clear_version_worker(self) -> None:
        if self.version_worker:
            self.version_worker.deleteLater()
            self.version_worker = None

    def _on_update_available(self, latest: str) -> None:
        message = self.t("gui.version.update_available", latest=latest, current=self.current_version)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(self.t("gui.version.update_title"))
        box.setText(message)
        open_button = box.addButton(self.t("gui.version.open_repo"), QMessageBox.AcceptRole)
        close_button = box.addButton(self.t("gui.buttons.ok"), QMessageBox.RejectRole)
        box.setDefaultButton(close_button)
        box.exec()
        if box.clickedButton() == open_button:
            QDesktopServices.openUrl(QUrl(REPOSITORY_URL))

    def _show_message(self, text: str, title_key: str = "gui.dialog.info") -> None:
        QMessageBox.information(self, self.t(title_key), text)

    def _show_error(self, text: str, title_key: str = "gui.dialog.error") -> None:
        QMessageBox.warning(self, self.t(title_key), text)

    def _format_game_label(self, app_id: int) -> str:
        return self.app_directory.format_entry(app_id)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.idler_ui:
            self.idler_ui.trigger_stop()
        if self.idler_worker and self.idler_worker.isRunning():
            self.idler_worker.wait(3000)
        if self.version_worker and self.version_worker.isRunning():
            self.version_worker.requestInterruption()
            self.version_worker.wait(1000)
        event.accept()

    def _needs_initial_setup(self) -> bool:
        return not (self.config.language and self.config.account.username and self.config.account.password)

    def _run_initial_setup(self) -> bool:
        dialog = InitialSetupDialog(Localization(self.localization.language))
        if dialog.exec() != QDialog.Accepted:
            return False
        language, username, password = dialog.result_values()
        self.localization.set_language(language)
        self.config.language = language
        self.config.account.username = username
        self.config.account.password = password
        self.config_manager.save(self.config)
        return True


def run() -> None:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    app = QApplication(sys.argv)
    
    icon_path = _get_icon_path()
    if icon_path:
        app.setWindowIcon(QIcon(str(icon_path)))
    
    _apply_dark_theme(app)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


def _apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")

    palette = QPalette()
    foreground = QColor(222, 222, 222)
    disabled_fg = QColor(140, 140, 140)
    highlight = QColor(61, 125, 216)
    window_bg = QColor(24, 24, 24)
    base_bg = QColor(18, 18, 18)
    button_bg = QColor(38, 38, 38)

    palette.setColor(QPalette.Window, window_bg)
    palette.setColor(QPalette.WindowText, foreground)
    palette.setColor(QPalette.Base, base_bg)
    palette.setColor(QPalette.AlternateBase, QColor(30, 30, 30))
    palette.setColor(QPalette.ToolTipBase, QColor(45, 45, 45))
    palette.setColor(QPalette.ToolTipText, foreground)
    palette.setColor(QPalette.Text, foreground)
    palette.setColor(QPalette.Button, button_bg)
    palette.setColor(QPalette.ButtonText, foreground)
    palette.setColor(QPalette.BrightText, QColor(255, 85, 85))
    palette.setColor(QPalette.Highlight, highlight)
    palette.setColor(QPalette.HighlightedText, QColor(0, 0, 0))
    palette.setColor(QPalette.Link, QColor(114, 159, 207))
    palette.setColor(QPalette.LinkVisited, QColor(172, 146, 235))
    palette.setColor(QPalette.Shadow, QColor(0, 0, 0))

    palette.setColor(QPalette.Disabled, QPalette.Text, disabled_fg)
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, disabled_fg)
    palette.setColor(QPalette.Disabled, QPalette.WindowText, disabled_fg)
    palette.setColor(QPalette.Disabled, QPalette.Highlight, QColor(55, 55, 55))
    palette.setColor(QPalette.Disabled, QPalette.HighlightedText, QColor(170, 170, 170))

    app.setPalette(palette)
    app.setStyleSheet(_DARK_STYLE_SHEET)


if __name__ == "__main__":
    run()

