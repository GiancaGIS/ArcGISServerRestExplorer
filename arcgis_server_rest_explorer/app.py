import csv
import json
import logging
import os
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.sax.saxutils import escape

import httpx
from PySide6.QtCore import Qt, QTimer, QUrl, qVersion
from PySide6.QtGui import QAction, QColor, QDesktopServices
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __author__, __version__
from . import arcgis_geometry as geom_utils
from .basemaps import BASEMAPS
from .credentials import (
    KEYRING_AVAILABLE,
    delete_credentials,
    delete_token,
    get_saved_credentials,
    get_saved_token,
    save_credentials,
    save_token,
)
from .dialogs import ConnectionAuthDialog, GenerateTokenDialog, GeometryLabDialog
from .map_bridge import MapBridge
from .map_html import build_leaflet_map_html
from .map_styles import MAP_STYLE_PRESETS, arcgis_color_to_hex, build_leaflet_style_from_renderer
from .models import ArcGISNodeData
from .operations import (
    build_gp_task_summary,
    default_gp_input_params,
    gp_operation_definitions,
    layer_operation_definitions,
    normalize_gp_input_params,
)
from .query_utils import build_query_params
from .storage import atomic_write_json, backup_corrupt_json, load_json_file
from .workers import (
    DEFAULT_HTTP_READ_TIMEOUT_SECONDS,
    FetchAllWorker,
    GpJobWorker,
    HttpWorker,
)

try:
    from PySide6.QtWebEngineCore import QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    WEBENGINE_AVAILABLE = True
except Exception:
    QWebEngineSettings = None
    QWebEngineView = None
    WEBENGINE_AVAILABLE = False


PACKAGE_DIR = Path(__file__).resolve().parent
LEGACY_APP_DIR = PACKAGE_DIR.parent


def get_app_data_dir() -> Path:
    override = os.environ.get("ARCGIS_REST_EXPLORER_HOME")
    if override:
        return Path(override).expanduser().resolve()
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "ArcGISRestExplorer"
    return Path.home() / ".arcgis_rest_explorer"


APP_DIR = get_app_data_dir()
APP_DIR.mkdir(parents=True, exist_ok=True)
CONNECTIONS_FILE = APP_DIR / "connections.json"
COLLECTIONS_FILE = APP_DIR / "collections.json"
HISTORY_FILE = APP_DIR / "history.json"
SETTINGS_FILE = APP_DIR / "setting.json"
GEOMETRY_HISTORY_FILE = APP_DIR / "geometry_history.json"
LOG_FILE = APP_DIR / "arcgis_rest_explorer.log"
MAP_HTML_FILE = APP_DIR / "map_preview.html"
MAP_SETHTML_MAX_CHARS = 1_500_000


def migrate_legacy_data_files() -> None:
    for filename in ("connections.json", "collections.json", "history.json", "setting.json", "geometry_history.json"):
        source = LEGACY_APP_DIR / filename
        target = APP_DIR / filename
        if source.exists() and not target.exists():
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


migrate_legacy_data_files()

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

JSON_FEATURE_PREVIEW_LIMIT = 100
TABLE_FEATURE_INITIAL_CHUNK_SIZE = 1000
TABLE_FEATURE_CHUNK_SIZE = 1000



class ArcGISRestExplorer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.query_builder_fields = None
        self.setWindowTitle(f"ArcGIS Server REST Explorer v{__version__}")
        self.resize_to_available_screen(1760, 1000)

        self.current_layer_url: str | None = None
        self.current_layer_metadata: dict[str, Any] | None = None
        self.current_layer_metadata_url: str | None = None
        self.current_operation_url: str | None = None
        self.current_operation_kind: str | None = None
        self.current_operation_metadata: dict[str, Any] | None = None
        self.last_response: dict[str, Any] | list[Any] | None = None
        self.last_geojson_features: list[dict[str, Any]] = []
        self.query_features: list[dict[str, Any]] = []
        self.query_table_columns: list[str] = []
        self.rendered_table_feature_count = 0
        self.rendered_map_feature_count = 0
        self.loading_table_chunk = False
        self.worker: HttpWorker | None = None
        self.fetch_all_worker: FetchAllWorker | None = None
        self.gp_job_worker: GpJobWorker | None = None
        self.workers: dict[int, HttpWorker] = {}
        self.request_counter = 0
        self.active_request_id = 0
        self.gp_job_request_id = 0
        self.connections: list[dict[str, str]] = []
        self.collections: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.geometry_history: list[dict[str, Any]] = []
        self.table_selection_from_map = False
        self.last_request_url = ""
        self.last_request_elapsed_ms: float | None = None
        self.last_query_out_wkid: int | None = None
        self.spatial_filter_geometry: dict[str, Any] | None = None
        self.spatial_filter_geometry_type: str | None = None
        self.spatial_filter_geojson_feature: dict[str, Any] | None = None
        self.fetch_all_request_id = 0
        self.current_theme = "Dark"
        self.map_style_preset = "ArcGIS renderer"
        self.http_read_timeout_seconds = DEFAULT_HTTP_READ_TIMEOUT_SECONDS
        self.verify_ssl = True
        self.google_maps_api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
        self.current_auth_config: dict[str, Any] = self.default_auth_config()

        self.map_bridge = MapBridge()
        self.map_bridge.featureClicked.connect(self.on_map_feature_clicked)
        self.map_bridge.areaDrawn.connect(self.on_map_area_drawn)
        self.map_bridge.polygonDrawn.connect(self.on_map_polygon_drawn)

        self._build_ui()
        self._build_menu()
        self._apply_dark_theme()
        self.load_settings()
        self.load_connections()
        self.load_collections()
        self.load_history()
        self.load_geometry_history()
        self.init_map()

    def resize_to_available_screen(self, preferred_width: int, preferred_height: int):
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        if screen is None:
            self.resize(max(900, preferred_width), max(650, preferred_height))
            return

        available = screen.availableGeometry()
        safe_width = max(640, available.width() - 40)
        safe_height = max(520, available.height() - 60)
        width = min(max(900, int(preferred_width)), safe_width)
        height = min(max(650, int(preferred_height)), safe_height)
        self.resize(width, height)

    def horizontal_scroll_area(self, widget: QWidget, max_height: int | None = None) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(widget)
        if max_height is not None:
            scroll.setMaximumHeight(max_height)
        return scroll

    def wrap_scroll_area(self, widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def apply_adaptive_splitter_sizes(self):
        if not all(hasattr(self, attr) for attr in ("main_splitter", "mid_splitter", "right_splitter")):
            return

        width = self.width()
        height = self.height()
        if width < 1200:
            self.main_splitter.setSizes([230, 360, 520])
            self.mid_splitter.setSizes([max(300, int(height * 0.48)), max(220, int(height * 0.34))])
            if self.right_splitter.count() > 1:
                self.right_splitter.setSizes([max(360, int(height * 0.60)), max(180, int(height * 0.25))])
        elif width < 1500:
            self.main_splitter.setSizes([280, 470, 680])
            self.mid_splitter.setSizes([420, 330])
            if self.right_splitter.count() > 1:
                self.right_splitter.setSizes([520, 280])
        else:
            self.main_splitter.setSizes([320, 610, 830])
            self.mid_splitter.setSizes([500, 430])
            if self.right_splitter.count() > 1:
                self.right_splitter.setSizes([620, 360])

    def _build_menu(self):
        menu = self.menuBar()
        file_menu = menu.addMenu("File")

        export_json_action = QAction("Export JSON response...", self)
        export_json_action.triggered.connect(self.export_json)
        file_menu.addAction(export_json_action)

        export_csv_action = QAction("Export table CSV...", self)
        export_csv_action.triggered.connect(self.export_csv)
        file_menu.addAction(export_csv_action)

        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        map_menu = menu.addMenu("Map")
        clear_map_action = QAction("Clear map", self)
        clear_map_action.triggered.connect(self.init_map)
        map_menu.addAction(clear_map_action)

        auth_menu = menu.addMenu("Auth")
        generate_token_action = QAction("Generate ArcGIS token...", self)
        generate_token_action.triggered.connect(self.open_generate_token_dialog)
        auth_menu.addAction(generate_token_action)

        clear_token_action = QAction("Clear current token", self)
        clear_token_action.triggered.connect(self.clear_current_token)
        auth_menu.addAction(clear_token_action)

        forget_token_action = QAction("Forget saved token", self)
        forget_token_action.triggered.connect(self.forget_saved_token)
        auth_menu.addAction(forget_token_action)

        tools_menu = menu.addMenu("Tools")
        copy_url_action = QAction("Copy last request URL", self)
        copy_url_action.triggered.connect(self.copy_last_request_url)
        tools_menu.addAction(copy_url_action)

        clear_history_action = QAction("Clear request history", self)
        clear_history_action.triggered.connect(self.clear_history)
        tools_menu.addAction(clear_history_action)

        open_log_action = QAction("Open log file", self)
        open_log_action.triggered.connect(self.open_log_file)
        tools_menu.addAction(open_log_action)

        geometry_lab_action = QAction("Open Geometry Lab...", self)
        geometry_lab_action.triggered.connect(self.open_geometry_lab)
        tools_menu.addAction(geometry_lab_action)

        tools_menu.addSeparator()
        settings_action = QAction("Program Settings...", self)
        settings_action.triggered.connect(self.open_program_settings)
        tools_menu.addAction(settings_action)

        help_menu = menu.addMenu("Help")
        about_action = QAction("About ArcGIS Server REST Explorer", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)

    def show_about_dialog(self):
        QMessageBox.about(
            self,
            "About ArcGIS Server REST Explorer",
            (
                f"<h3>ArcGIS Server REST Explorer</h3>"
                f"<p><b>Version:</b> {__version__}</p>"
                f"<p><b>Author:</b> {__author__}</p>"
                "<p>Desktop explorer for ArcGIS Server REST services, queries, feature tables, "
                "map preview, exports, and geometry tools.</p>"
                f"<p><b>Python:</b> {sys.version.split()[0]}<br>"
                f"<b>Qt:</b> {qVersion()}</p>"
            ),
        )

    def open_log_file(self):
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            LOG_FILE.touch(exist_ok=True)
        except Exception as exc:
            QMessageBox.critical(self, "Open log file", f"Could not create log file:\n{exc}")
            return

        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_FILE))):
            QMessageBox.warning(self, "Open log file", f"Could not open log file:\n{LOG_FILE}")

    def _build_ui(self):
        root = QWidget()
        root_layout = QVBoxLayout(root)

        conn_widget = QWidget()
        conn_layout = QVBoxLayout(conn_widget)
        conn_layout.setContentsMargins(0, 0, 0, 0)
        conn_layout.setSpacing(6)
        self.connection_combo = QComboBox()
        self.connection_combo.setMinimumWidth(140)
        self.connection_combo.currentIndexChanged.connect(self.on_connection_selected)

        self.connection_name = QLineEdit()
        self.connection_name.setMinimumWidth(150)
        self.connection_name.setPlaceholderText("Connection name")

        self.base_url = QLineEdit("https://sampleserver6.arcgisonline.com/arcgis/rest/services")
        self.base_url.setMinimumWidth(280)
        self.base_url.setPlaceholderText("ArcGIS Server REST services URL")

        self.token_input = QLineEdit()
        self.token_input.setMinimumWidth(160)
        self.token_input.setPlaceholderText("Token optional")
        self.token_input.setEchoMode(QLineEdit.Password)
        self.token_input.textChanged.connect(lambda *_: self.update_auth_status("session_only"))
        self.token_expiry_label = QLabel("Token: not checked")
        self.auth_status_label = QLabel("Auth: no token")

        self.save_conn_btn = QPushButton("Save")
        self.save_conn_btn.clicked.connect(self.save_current_connection)
        self.delete_conn_btn = QPushButton("Delete")
        self.delete_conn_btn.clicked.connect(self.delete_current_connection)
        self.auth_settings_btn = QPushButton("Auth")
        self.auth_settings_btn.clicked.connect(self.open_connection_auth_settings)
        self.generate_token_btn = QPushButton("Generate Token...")
        self.generate_token_btn.clicked.connect(self.open_generate_token_dialog)
        self.clear_token_btn = QPushButton("Clear Token")
        self.clear_token_btn.clicked.connect(self.clear_current_token)
        self.forget_token_btn = QPushButton("Forget")
        self.forget_token_btn.clicked.connect(self.forget_saved_token)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.load_services_root)

        conn_row = QHBoxLayout()
        conn_row.setSpacing(6)
        conn_row.addWidget(QLabel("Connection"))
        conn_row.addWidget(self.connection_combo, 1)
        conn_row.addWidget(QLabel("Name"))
        conn_row.addWidget(self.connection_name, 1)
        conn_row.addWidget(QLabel("URL"))
        conn_row.addWidget(self.base_url, 4)
        conn_row.addWidget(self.connect_btn)

        auth_row = QHBoxLayout()
        auth_row.setSpacing(6)
        auth_row.addWidget(QLabel("Token"))
        auth_row.addWidget(self.token_input, 3)
        auth_row.addWidget(self.token_expiry_label)
        auth_row.addWidget(self.auth_status_label)
        auth_row.addStretch(1)
        auth_row.addWidget(self.save_conn_btn)
        auth_row.addWidget(self.delete_conn_btn)
        auth_row.addWidget(self.auth_settings_btn)
        auth_row.addWidget(self.generate_token_btn)
        auth_row.addWidget(self.clear_token_btn)
        auth_row.addWidget(self.forget_token_btn)

        conn_layout.addLayout(conn_row)
        conn_layout.addLayout(auth_row)
        conn_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        root_layout.addWidget(conn_widget, 0)

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.setChildrenCollapsible(True)
        self.main_splitter = main_splitter

        left = QWidget()
        left.setMinimumWidth(220)
        left_layout = QVBoxLayout(left)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Services")
        self.tree.itemExpanded.connect(self.on_item_expanded)
        self.tree.itemClicked.connect(self.on_item_clicked)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.open_catalog_context_menu)
        left_layout.addWidget(QLabel("Catalog"))
        left_layout.addWidget(self.tree, 3)

        collections_bar = QHBoxLayout()
        collections_bar.setContentsMargins(0, 0, 0, 0)
        collections_bar.setSpacing(8)
        self.save_query_btn = QPushButton("Save Call")
        self.save_query_btn.clicked.connect(self.save_current_call_to_collection)
        self.load_query_btn = QPushButton("Load")
        self.load_query_btn.clicked.connect(self.load_selected_collection_query)
        self.delete_query_btn = QPushButton("Delete")
        self.delete_query_btn.clicked.connect(self.delete_selected_collection_query)
        collections_bar.addWidget(self.save_query_btn)
        collections_bar.addWidget(self.load_query_btn)
        collections_bar.addWidget(self.delete_query_btn)

        self.collections_tree = QTreeWidget()
        self.collections_tree.setHeaderLabels(["Saved Calls", "Target"])
        self.collections_tree.itemDoubleClicked.connect(lambda *_: self.load_selected_collection_call())

        self.history_tree = QTreeWidget()
        self.history_tree.setHeaderLabels(["History", "ms"])
        self.history_tree.itemDoubleClicked.connect(self.copy_selected_history_url)

        collections_panel = QWidget()
        collections_layout = QVBoxLayout(collections_panel)
        collections_layout.setContentsMargins(8, 12, 8, 8)
        collections_layout.setSpacing(8)
        collections_layout.addLayout(collections_bar)
        collections_layout.addWidget(self.collections_tree)

        history_panel = QWidget()
        history_layout = QVBoxLayout(history_panel)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.addWidget(self.history_tree)

        self.left_activity_tabs = QTabWidget()
        self.left_activity_tabs.addTab(collections_panel, "Saved Calls")
        self.left_activity_tabs.addTab(history_panel, "History")
        left_layout.addWidget(self.left_activity_tabs, 2)
        main_splitter.addWidget(left)

        mid_splitter = QSplitter(Qt.Vertical)
        mid_splitter.setChildrenCollapsible(True)
        self.mid_splitter = mid_splitter

        query_panel = QWidget()
        query_layout = QVBoxLayout(query_panel)

        query_form = QFormLayout()
        self.where_input = QLineEdit("1=1")
        self.out_fields = QLineEdit("*")

        self.query_builder_fields: list[dict[str, Any]] = []
        self.condition_table = QTableWidget()
        self.condition_table.setColumnCount(5)
        self.condition_table.setHorizontalHeaderLabels(["Join", "Field", "Operator", "Value", ""])
        self.condition_table.setMinimumHeight(150)
        self.condition_table.verticalHeader().setVisible(False)
        self.condition_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.condition_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.condition_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.condition_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.condition_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)

        self.where_preview = QTextEdit()
        self.where_preview.setReadOnly(True)
        self.where_preview.setMaximumHeight(70)
        self.where_preview.setPlaceholderText("Visual WHERE preview")

        builder_buttons = QHBoxLayout()
        self.add_condition_btn = QPushButton("Add Condition")
        self.add_condition_btn.clicked.connect(self.add_condition_row)
        self.apply_where_btn = QPushButton("Apply WHERE")
        self.apply_where_btn.clicked.connect(self.apply_visual_where)
        self.reset_where_btn = QPushButton("Reset")
        self.reset_where_btn.clicked.connect(self.reset_visual_builder)
        builder_buttons.addWidget(self.add_condition_btn)
        builder_buttons.addWidget(self.apply_where_btn)
        builder_buttons.addWidget(self.reset_where_btn)

        self.return_geometry = QCheckBox("Return geometry")
        self.return_geometry.setChecked(True)
        self.fetch_all_pages = QCheckBox("Fetch all pages if transfer limit is exceeded")
        self.max_records = QComboBox()
        self.max_records.addItems(["10", "100", "500", "1000", "2000", "5000"])
        self.order_by = QLineEdit()
        self.order_by.setPlaceholderText("Example: OBJECTID DESC")

        self.use_spatial_filter = QCheckBox("Use Geometry Lab spatial filter")
        self.spatial_filter_label = QLabel("Spatial filter: none")
        self.clear_spatial_filter_btn = QPushButton("Clear Spatial Filter")
        self.clear_spatial_filter_btn.clicked.connect(self.clear_spatial_filter)

        self.query_btn = QPushButton("Run Query")
        self.query_btn.clicked.connect(self.run_query)
        self.stop_query_btn = QPushButton("Stop")
        self.stop_query_btn.setObjectName("stopQueryButton")
        self.stop_query_btn.setToolTip("Stop the active request and ignore late responses")
        self.stop_query_btn.clicked.connect(self.stop_active_request)
        self.stop_query_btn.hide()
        query_buttons = QHBoxLayout()
        query_buttons.addWidget(self.query_btn)
        query_buttons.addWidget(self.stop_query_btn)

        query_form.addRow("Where", self.where_input)
        query_form.addRow("Visual builder", self.condition_table)
        query_form.addRow("Preview", self.where_preview)
        query_form.addRow("", builder_buttons)
        query_form.addRow("Out fields", self.out_fields)
        query_form.addRow("", self.return_geometry)
        query_form.addRow("", self.fetch_all_pages)
        query_form.addRow("Limit", self.max_records)
        query_form.addRow("Order by", self.order_by)
        query_form.addRow("", self.use_spatial_filter)
        query_form.addRow("Spatial", self.spatial_filter_label)
        query_form.addRow("", self.clear_spatial_filter_btn)
        query_form.addRow("", query_buttons)

        self.metadata_text = QTextEdit()
        self.metadata_text.setReadOnly(True)
        self.metadata_text.setMaximumHeight(70)
        self.metadata_text.setPlaceholderText("Compact layer metadata. Right-click a layer in the catalog for full metadata.")
        self.metadata_text.setVisible(False)
        self.toggle_metadata_btn = QPushButton("Show Compact Metadata")
        self.toggle_metadata_btn.clicked.connect(self.toggle_compact_metadata)
        self.map_status = QTextEdit()
        self.map_status.setReadOnly(True)
        self.map_status.setMaximumHeight(145)

        query_layout.addLayout(query_form)
        query_layout.addWidget(QLabel("Geometry / Map Status"))
        query_layout.addWidget(self.map_status)
        self.metadata_label = QLabel("Layer Metadata (compact)")
        self.metadata_label.setVisible(False)
        query_layout.addWidget(self.toggle_metadata_btn)
        query_layout.addWidget(self.metadata_label)
        query_layout.addWidget(self.metadata_text)

        operations_panel = QWidget()
        operations_layout = QVBoxLayout(operations_panel)
        operations_form = QFormLayout()
        self.operation_target_label = QLabel("No REST operation target selected")
        self.operation_combo = QComboBox()
        self.operation_combo.currentIndexChanged.connect(self.on_operation_changed)
        self.operation_params = QTextEdit()
        self.operation_params.setMinimumHeight(160)
        self.operation_params.setPlaceholderText('JSON parameters, for example: {"where": "1=1"}')
        self.run_operation_btn = QPushButton("Run Operation")
        self.run_operation_btn.clicked.connect(self.run_selected_operation)
        self.operation_status = QTextEdit()
        self.operation_status.setReadOnly(True)
        self.operation_status.setMaximumHeight(120)
        self.operation_output = QTextEdit()
        self.operation_output.setReadOnly(True)
        self.operation_output.setMinimumHeight(180)
        operations_form.addRow("Target", self.operation_target_label)
        operations_form.addRow("Operation", self.operation_combo)
        operations_form.addRow("Parameters", self.operation_params)
        operations_form.addRow("", self.run_operation_btn)
        operations_layout.addLayout(operations_form)
        operations_layout.addWidget(QLabel("Job / Operation Status"))
        operations_layout.addWidget(self.operation_status)
        operations_layout.addWidget(QLabel("Operation Output"))
        operations_layout.addWidget(self.operation_output)
        self.update_operation_panel(None, None, None)

        self.request_tabs = QTabWidget()
        self.request_tabs.addTab(self.wrap_scroll_area(query_panel), "Query")
        self.request_tabs.addTab(self.wrap_scroll_area(operations_panel), "Operations")
        mid_splitter.addWidget(self.request_tabs)

        self.table = QTableWidget()
        self.table.itemSelectionChanged.connect(self.on_table_selection_changed)
        self.table.verticalScrollBar().valueChanged.connect(self.on_table_scroll)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.open_table_context_menu)
        table_panel = QWidget()
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(self.table)

        json_panel = QWidget()
        json_layout = QVBoxLayout(json_panel)
        json_layout.setContentsMargins(0, 0, 0, 0)
        self.response_text = QTextEdit()
        self.response_text.setReadOnly(True)
        self.response_text.setContextMenuPolicy(Qt.CustomContextMenu)
        self.response_text.customContextMenuRequested.connect(self.open_json_response_context_menu)
        json_layout.addWidget(self.response_text)

        self.result_tabs = QTabWidget()
        self.result_tabs.addTab(table_panel, "Attribute Table")
        self.result_tabs.addTab(json_panel, "JSON Response")
        mid_splitter.addWidget(self.result_tabs)
        main_splitter.addWidget(mid_splitter)

        right_splitter = QSplitter(Qt.Vertical)
        right_splitter.setChildrenCollapsible(True)
        self.right_splitter = right_splitter
        map_panel = QWidget()
        map_layout = QVBoxLayout(map_panel)

        map_top_widget = QWidget()
        map_top = QHBoxLayout(map_top_widget)
        map_top.setContentsMargins(0, 0, 0, 0)
        map_top.setSpacing(6)
        map_top.addWidget(QLabel("Map Preview"))
        self.draw_area_filter_btn = QPushButton("Draw Rectangle Filter")
        self.draw_area_filter_btn.clicked.connect(self.enable_map_area_drawing)
        self.draw_polygon_filter_btn = QPushButton("Draw Polygon Filter")
        self.draw_polygon_filter_btn.clicked.connect(self.enable_map_polygon_drawing)
        self.basemap_combo = QComboBox()
        self.basemap_combo.addItems(list(BASEMAPS))
        self.basemap_combo.currentTextChanged.connect(self.on_basemap_changed)
        self.map_style_combo = QComboBox()
        self.populate_map_style_combo(self.map_style_combo)
        self.map_style_combo.currentTextChanged.connect(self.on_map_style_changed)
        map_top.addStretch(1)
        map_top.addWidget(self.draw_area_filter_btn)
        map_top.addWidget(self.draw_polygon_filter_btn)
        map_top.addWidget(QLabel("Style:"))
        map_top.addWidget(self.map_style_combo)
        map_top.addWidget(QLabel("Basemap:"))
        map_top.addWidget(self.basemap_combo)
        map_layout.addWidget(self.horizontal_scroll_area(map_top_widget, 62))

        if WEBENGINE_AVAILABLE:
            self.map_view = QWebEngineView()
            if QWebEngineSettings is not None:
                settings = self.map_view.settings()
                settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
                settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
            self.web_channel = QWebChannel(self.map_view.page())
            self.web_channel.registerObject("bridge", self.map_bridge)
            self.map_view.page().setWebChannel(self.web_channel)
            map_layout.addWidget(self.map_view, 4)
        else:
            self.map_view = None
            self.web_channel = None
            fallback = QTextEdit()
            fallback.setReadOnly(True)
            fallback.setText("QtWebEngine non disponibile.\nInstalla PySide6-Addons.")
            map_layout.addWidget(fallback)

        self.geometry_lab_panel = QWidget()
        geometry_lab_layout = QVBoxLayout(self.geometry_lab_panel)
        geometry_lab_layout.setContentsMargins(0, 8, 0, 0)

        geometry_lab_header_widget = QWidget()
        geometry_lab_header = QHBoxLayout(geometry_lab_header_widget)
        geometry_lab_header.setContentsMargins(0, 0, 0, 0)
        geometry_lab_header.setSpacing(6)
        geometry_lab_header.addWidget(QLabel("Geometry Lab"))
        self.toggle_geometry_lab_btn = QPushButton("Expand")
        self.toggle_geometry_lab_btn.clicked.connect(self.toggle_geometry_lab)
        self.geometry_lab_format_combo = QComboBox()
        self.geometry_lab_format_combo.addItems(["Auto", "GeoJSON", "ArcGIS JSON", "WKT"])
        self.geometry_lab_spatial_rel_combo = QComboBox()
        self.geometry_lab_spatial_rel_combo.addItems([
            "esriSpatialRelIntersects",
            "esriSpatialRelContains",
            "esriSpatialRelWithin",
            "esriSpatialRelTouches",
            "esriSpatialRelCrosses",
            "esriSpatialRelOverlaps",
            "esriSpatialRelEnvelopeIntersects",
        ])
        self.geometry_lab_input_sr_combo = QComboBox()
        self.geometry_lab_input_sr_combo.addItems(["Auto", "EPSG:4326", "EPSG:3857 / 102100"])
        self.geometry_lab_history_combo = QComboBox()
        self.geometry_lab_history_combo.addItem("-- geometry history --", None)
        self.geometry_lab_history_combo.currentIndexChanged.connect(self.load_selected_geometry_history)
        geometry_lab_header.addStretch(1)
        geometry_lab_header.addWidget(self.toggle_geometry_lab_btn)
        geometry_lab_header.addWidget(QLabel("Relation:"))
        geometry_lab_header.addWidget(self.geometry_lab_spatial_rel_combo)
        geometry_lab_header.addWidget(QLabel("Input SR:"))
        geometry_lab_header.addWidget(self.geometry_lab_input_sr_combo)
        geometry_lab_header.addWidget(QLabel("History:"))
        geometry_lab_header.addWidget(self.geometry_lab_history_combo)
        geometry_lab_header.addWidget(QLabel("Format:"))
        geometry_lab_header.addWidget(self.geometry_lab_format_combo)
        geometry_lab_layout.addWidget(self.horizontal_scroll_area(geometry_lab_header_widget, 62))

        self.geometry_lab_body = QWidget()
        geometry_lab_body_layout = QVBoxLayout(self.geometry_lab_body)
        geometry_lab_body_layout.setContentsMargins(0, 0, 0, 0)

        self.geometry_lab_input = QTextEdit()
        self.geometry_lab_input.setMaximumHeight(115)
        self.geometry_lab_input.setPlaceholderText(
            "Paste WKT, GeoJSON or ArcGIS JSON. Example: POINT (12.4924 41.8902)"
        )
        geometry_lab_body_layout.addWidget(self.geometry_lab_input)

        geometry_lab_buttons_widget = QWidget()
        geometry_lab_buttons = QHBoxLayout(geometry_lab_buttons_widget)
        geometry_lab_buttons.setContentsMargins(0, 0, 0, 0)
        geometry_lab_buttons.setSpacing(6)
        self.geometry_lab_sample_point_btn = QPushButton("Sample Point")
        self.geometry_lab_sample_point_btn.clicked.connect(self.load_geometry_lab_sample_point)
        self.geometry_lab_sample_polygon_btn = QPushButton("Sample Polygon")
        self.geometry_lab_sample_polygon_btn.clicked.connect(self.load_geometry_lab_sample_polygon)
        self.geometry_lab_preview_btn = QPushButton("Preview")
        self.geometry_lab_preview_btn.clicked.connect(self.preview_geometry_lab)
        self.geometry_lab_use_btn = QPushButton("Use In Query")
        self.geometry_lab_use_btn.clicked.connect(self.use_geometry_lab_as_spatial_filter)
        self.geometry_lab_clear_btn = QPushButton("Clear Lab")
        self.geometry_lab_clear_btn.clicked.connect(self.clear_geometry_lab)
        self.geometry_lab_import_btn = QPushButton("Import")
        self.geometry_lab_import_btn.clicked.connect(self.import_geometry_lab)
        self.geometry_lab_export_btn = QPushButton("Export")
        self.geometry_lab_export_btn.clicked.connect(self.export_geometry_lab)
        self.geometry_lab_copy_geojson_btn = QPushButton("Copy GeoJSON")
        self.geometry_lab_copy_geojson_btn.clicked.connect(lambda: self.copy_geometry_lab_output("geojson"))
        self.geometry_lab_copy_arcgis_btn = QPushButton("Copy ArcGIS")
        self.geometry_lab_copy_arcgis_btn.clicked.connect(lambda: self.copy_geometry_lab_output("arcgis"))
        self.geometry_lab_copy_params_btn = QPushButton("Copy Params")
        self.geometry_lab_copy_params_btn.clicked.connect(lambda: self.copy_geometry_lab_output("params"))
        geometry_lab_buttons.addWidget(self.geometry_lab_sample_point_btn)
        geometry_lab_buttons.addWidget(self.geometry_lab_sample_polygon_btn)
        geometry_lab_buttons.addWidget(self.geometry_lab_import_btn)
        geometry_lab_buttons.addWidget(self.geometry_lab_export_btn)
        geometry_lab_buttons.addWidget(self.geometry_lab_preview_btn)
        geometry_lab_buttons.addWidget(self.geometry_lab_use_btn)
        geometry_lab_buttons.addWidget(self.geometry_lab_clear_btn)
        geometry_lab_body_layout.addWidget(self.horizontal_scroll_area(geometry_lab_buttons_widget, 62))

        geometry_lab_copy_buttons_widget = QWidget()
        geometry_lab_copy_buttons = QHBoxLayout(geometry_lab_copy_buttons_widget)
        geometry_lab_copy_buttons.setContentsMargins(0, 0, 0, 0)
        geometry_lab_copy_buttons.setSpacing(6)
        geometry_lab_copy_buttons.addWidget(self.geometry_lab_copy_geojson_btn)
        geometry_lab_copy_buttons.addWidget(self.geometry_lab_copy_arcgis_btn)
        geometry_lab_copy_buttons.addWidget(self.geometry_lab_copy_params_btn)
        geometry_lab_body_layout.addWidget(self.horizontal_scroll_area(geometry_lab_copy_buttons_widget, 62))

        self.geometry_lab_summary = QTextEdit()
        self.geometry_lab_summary.setReadOnly(True)
        self.geometry_lab_summary.setMaximumHeight(80)
        self.geometry_lab_summary.setPlaceholderText("Geometry summary will appear here...")
        geometry_lab_body_layout.addWidget(self.geometry_lab_summary)

        self.geometry_lab_output = QTextEdit()
        self.geometry_lab_output.setReadOnly(True)
        self.geometry_lab_output.setMaximumHeight(100)
        self.geometry_lab_output.setPlaceholderText("ArcGIS Server REST geometry params will appear here...")
        geometry_lab_body_layout.addWidget(self.geometry_lab_output)
        geometry_lab_layout.addWidget(self.geometry_lab_body)
        self.set_geometry_lab_expanded(False)

        map_layout.addWidget(self.geometry_lab_panel, 1)

        right_splitter.addWidget(map_panel)

        main_splitter.addWidget(right_splitter)
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 2)
        main_splitter.setStretchFactor(2, 3)
        mid_splitter.setStretchFactor(0, 3)
        mid_splitter.setStretchFactor(1, 2)
        right_splitter.setStretchFactor(0, 1)
        self.apply_adaptive_splitter_sizes()
        root_layout.addWidget(main_splitter, 1)

        self.statusBar().showMessage("Ready")
        self.setCentralWidget(root)

        self.toast_label = QLabel(self)
        self.toast_label.setObjectName("toastLabel")
        self.toast_label.setWordWrap(True)
        self.toast_label.setMinimumWidth(320)
        self.toast_label.setMaximumWidth(520)
        self.toast_label.hide()

    def _apply_dark_theme(self):
        self.current_theme = "Dark"
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #0f1720; color: #dbe7f3; font-size: 13px; }
            QMenuBar, QMenu { background: #0b1220; color: #dbe7f3; }
            QMenu::item:selected { background: #1d4ed8; }
            QLineEdit, QTextEdit, QTreeWidget, QTableWidget, QComboBox {
                background: #141f2b; color: #e8f1fb; border: 1px solid #28384a;
                border-radius: 6px; padding: 6px; selection-background-color: #2563eb;
            }
            QPushButton {
                background: #1d4ed8; color: white; border: 0; border-radius: 6px;
                padding: 8px 14px; font-weight: 600;
            }
            QPushButton:hover { background: #2563eb; }
            QPushButton:disabled { background: #334155; color: #94a3b8; }
            QPushButton#stopQueryButton {
                background: #dc2626; color: #ffffff; border: 0; border-radius: 5px;
                padding: 8px 14px; font-weight: 900;
            }
            QPushButton#stopQueryButton:hover { background: #ef4444; }
            QPushButton#stopQueryButton:disabled { background: #7f1d1d; color: #fecaca; }
            QLabel { color: #b8c7d9; }
            QHeaderView::section {
                background: #182536; color: #dbe7f3; padding: 6px; border: 1px solid #28384a;
            }
            QStatusBar { background: #0b1220; color: #9fb3c8; }
            QCheckBox { padding: 4px; }
            QTabWidget::pane { border: 1px solid #28384a; border-radius: 6px; top: -1px; }
            QTabBar::tab {
                background: #111827; color: #9fb3c8; border: 1px solid #28384a;
                padding: 8px 12px; border-top-left-radius: 6px; border-top-right-radius: 6px;
            }
            QTabBar::tab:selected { background: #1d4ed8; color: #ffffff; border-color: #2563eb; }
            QTabBar::tab:hover:!selected { background: #182536; color: #dbe7f3; }
            QLabel#toastLabel {
                background: #0f766e; color: #ecfeff; border: 1px solid #14b8a6;
                border-radius: 10px; padding: 12px 16px; font-weight: 700;
            }
        """)

    def _apply_light_theme(self):
        self.current_theme = "Light"
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f6f8fb; color: #172033; font-size: 13px; }
            QMenuBar, QMenu { background: #ffffff; color: #172033; border: 1px solid #d8e0ea; }
            QMenu::item:selected { background: #dbeafe; color: #0f172a; }
            QLineEdit, QTextEdit, QTreeWidget, QTableWidget, QComboBox {
                background: #ffffff; color: #172033; border: 1px solid #cbd5e1;
                border-radius: 6px; padding: 6px; selection-background-color: #bfdbfe;
            }
            QPushButton {
                background: #2563eb; color: white; border: 0; border-radius: 6px;
                padding: 8px 14px; font-weight: 600;
            }
            QPushButton:hover { background: #1d4ed8; }
            QPushButton:disabled { background: #d1d5db; color: #64748b; }
            QPushButton#stopQueryButton {
                background: #dc2626; color: #ffffff; border: 0; border-radius: 5px;
                padding: 8px 14px; font-weight: 900;
            }
            QPushButton#stopQueryButton:hover { background: #b91c1c; }
            QPushButton#stopQueryButton:disabled { background: #fecaca; color: #991b1b; }
            QLabel { color: #334155; }
            QHeaderView::section {
                background: #e2e8f0; color: #172033; padding: 6px; border: 1px solid #cbd5e1;
            }
            QStatusBar { background: #ffffff; color: #475569; border-top: 1px solid #d8e0ea; }
            QCheckBox { padding: 4px; }
            QTabWidget::pane { border: 1px solid #cbd5e1; border-radius: 6px; top: -1px; }
            QTabBar::tab {
                background: #e2e8f0; color: #475569; border: 1px solid #cbd5e1;
                padding: 8px 12px; border-top-left-radius: 6px; border-top-right-radius: 6px;
            }
            QTabBar::tab:selected { background: #2563eb; color: #ffffff; border-color: #2563eb; }
            QTabBar::tab:hover:!selected { background: #dbeafe; color: #0f172a; }
            QLabel#toastLabel {
                background: #ecfdf5; color: #065f46; border: 1px solid #34d399;
                border-radius: 10px; padding: 12px 16px; font-weight: 700;
            }
        """)

    def on_theme_changed(self, theme: str):
        if theme == "Light":
            self._apply_light_theme()
        else:
            self._apply_dark_theme()
        self.draw_features_on_map(self.last_geojson_features)

    def toggle_compact_metadata(self):
        visible = not self.metadata_text.isVisible()
        self.metadata_text.setVisible(visible)
        self.metadata_label.setVisible(visible)
        self.toggle_metadata_btn.setText("Hide Compact Metadata" if visible else "Show Compact Metadata")

    def set_geometry_lab_expanded(self, expanded: bool):
        self.geometry_lab_body.setVisible(expanded)
        self.toggle_geometry_lab_btn.setText("Collapse" if expanded else "Expand")

    def toggle_geometry_lab(self):
        self.set_geometry_lab_expanded(not self.geometry_lab_body.isVisible())

    @staticmethod
    def parse_timeout_setting(value: Any) -> int:
        try:
            return min(1800, max(30, int(value)))
        except (TypeError, ValueError):
            return DEFAULT_HTTP_READ_TIMEOUT_SECONDS

    def map_style_presets(self) -> dict[str, dict[str, Any]]:
        return MAP_STYLE_PRESETS

    def populate_map_style_combo(self, combo: QComboBox) -> None:
        combo.blockSignals(True)
        combo.clear()
        for name, preset in self.map_style_presets().items():
            combo.addItem(name)
            color = preset.get("fillColor") or preset.get("color")
            if color:
                combo.setItemData(combo.count() - 1, QColor(str(color)), Qt.ItemDataRole.DecorationRole)
        combo.blockSignals(False)
        self.sync_map_style_combo(combo)

    def sync_map_style_combo(self, combo: QComboBox | None = None) -> None:
        targets = [combo] if combo is not None else [getattr(self, "map_style_combo", None)]
        for target in targets:
            if target is None:
                continue
            ix = target.findText(self.map_style_preset)
            if ix >= 0 and target.currentIndex() != ix:
                target.blockSignals(True)
                target.setCurrentIndex(ix)
                target.blockSignals(False)

    def open_program_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Program Settings")
        dialog.resize(460, 260)

        layout = QVBoxLayout(dialog)
        form = QFormLayout()

        theme_combo = QComboBox()
        theme_combo.addItems(["Dark", "Light"])
        ix = theme_combo.findText(self.current_theme)
        if ix >= 0:
            theme_combo.setCurrentIndex(ix)

        map_style_combo = QComboBox()
        self.populate_map_style_combo(map_style_combo)

        http_timeout_spin = QSpinBox()
        http_timeout_spin.setRange(30, 1800)
        http_timeout_spin.setSuffix(" sec")
        http_timeout_spin.setValue(int(self.http_read_timeout_seconds))

        verify_ssl_check = QCheckBox("Verify SSL certificates")
        verify_ssl_check.setChecked(bool(self.verify_ssl))

        google_api_key_input = QLineEdit()
        google_api_key_input.setEchoMode(QLineEdit.Password)
        google_api_key_input.setText(self.google_maps_api_key)
        google_api_key_input.setPlaceholderText("Required for Google basemaps")

        form.addRow("Theme", theme_combo)
        form.addRow("Map object style", map_style_combo)
        form.addRow("HTTP read timeout", http_timeout_spin)
        form.addRow("REST SSL", verify_ssl_check)
        form.addRow("Google Maps API key", google_api_key_input)
        layout.addLayout(form)

        hint = QLabel(
            "Theme affects the full interface. Map object style applies to mapped query features. "
            "Disable SSL verification only for trusted ArcGIS/Portal endpoints with self-signed certificates. "
            "Google basemaps use the official Map Tiles API and require a Google Maps Platform API key."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() == QDialog.Accepted:
            selected_theme = theme_combo.currentText()
            if selected_theme != self.current_theme:
                self.on_theme_changed(selected_theme)
            self.map_style_preset = map_style_combo.currentText()
            self.sync_map_style_combo()
            self.http_read_timeout_seconds = http_timeout_spin.value()
            self.verify_ssl = verify_ssl_check.isChecked()
            self.google_maps_api_key = google_api_key_input.text().strip()
            self.draw_features_on_map(self.last_geojson_features)
            self.save_settings()
            ssl_status = "SSL verify on" if self.verify_ssl else "SSL verify off"
            self.statusBar().showMessage(
                f"Settings applied: {self.current_theme}, {self.map_style_preset}, "
                f"timeout {self.http_read_timeout_seconds}s, {ssl_status}"
            )

    # ---------------- Settings / Notifications ----------------

    def load_settings(self):
        settings, error = load_json_file(SETTINGS_FILE, {})
        if error:
            backup_path = backup_corrupt_json(SETTINGS_FILE)
            logger.warning("%s Backup: %s", error, backup_path)
            return
        if not isinstance(settings, dict):
            return

        width = settings.get("window_width")
        height = settings.get("window_height")
        if isinstance(width, int) and isinstance(height, int):
            self.resize_to_available_screen(width, height)

        self.on_theme_changed(str(settings.get("theme", "Dark")))

        basemap = settings.get("basemap")
        ix = self.basemap_combo.findText(str(basemap)) if basemap else -1
        if ix >= 0:
            self.basemap_combo.setCurrentIndex(ix)
        map_style_preset = settings.get("map_style_preset", "ArcGIS renderer")
        if map_style_preset in self.map_style_presets():
            self.map_style_preset = str(map_style_preset)
            self.sync_map_style_combo()
        self.http_read_timeout_seconds = self.parse_timeout_setting(settings.get("http_read_timeout_seconds"))
        self.verify_ssl = bool(settings.get("verify_ssl", True))
        self.google_maps_api_key = str(settings.get("google_maps_api_key") or os.environ.get("GOOGLE_MAPS_API_KEY", "")).strip()

        self.return_geometry.setChecked(bool(settings.get("return_geometry", True)))
        self.fetch_all_pages.setChecked(bool(settings.get("fetch_all_pages", False)))
        self.out_fields.setText(str(settings.get("out_fields", "*")))
        self.order_by.setText(str(settings.get("order_by", "")))
        spatial_rel = settings.get("geometry_lab_spatial_rel", "esriSpatialRelIntersects")
        ix = self.geometry_lab_spatial_rel_combo.findText(str(spatial_rel))
        if ix >= 0:
            self.geometry_lab_spatial_rel_combo.setCurrentIndex(ix)
        input_sr = settings.get("geometry_lab_input_sr", "Auto")
        ix = self.geometry_lab_input_sr_combo.findText(str(input_sr))
        if ix >= 0:
            self.geometry_lab_input_sr_combo.setCurrentIndex(ix)
        self.set_geometry_lab_expanded(bool(settings.get("geometry_lab_expanded", False)))

        max_records = str(settings.get("max_records", "100"))
        ix = self.max_records.findText(max_records)
        if ix >= 0:
            self.max_records.setCurrentIndex(ix)

        compact_metadata_visible = bool(settings.get("compact_metadata_visible", False))
        self.metadata_text.setVisible(compact_metadata_visible)
        self.metadata_label.setVisible(compact_metadata_visible)
        self.toggle_metadata_btn.setText(
            "Hide Compact Metadata" if compact_metadata_visible else "Show Compact Metadata"
        )
        self.apply_adaptive_splitter_sizes()

    def save_settings(self):
        settings = {
            "theme": self.current_theme,
            "basemap": self.basemap_combo.currentText(),
            "map_style_preset": self.map_style_preset,
            "http_read_timeout_seconds": self.http_read_timeout_seconds,
            "verify_ssl": self.verify_ssl,
            "google_maps_api_key": self.google_maps_api_key,
            "return_geometry": self.return_geometry.isChecked(),
            "fetch_all_pages": self.fetch_all_pages.isChecked(),
            "max_records": self.max_records.currentText(),
            "out_fields": self.out_fields.text().strip() or "*",
            "order_by": self.order_by.text().strip(),
            "geometry_lab_spatial_rel": self.geometry_lab_spatial_rel_combo.currentText(),
            "geometry_lab_input_sr": self.geometry_lab_input_sr_combo.currentText(),
            "geometry_lab_expanded": self.geometry_lab_body.isVisible(),
            "compact_metadata_visible": self.metadata_text.isVisible(),
            "window_width": self.width(),
            "window_height": self.height(),
        }
        atomic_write_json(SETTINGS_FILE, settings)

    def show_toast(self, message: str, timeout_ms: int = 3500, kind: str = "success"):
        if not hasattr(self, "toast_label"):
            return
        self.toast_label.setText(message)
        self.toast_label.setStyleSheet(self.toast_style(kind))
        self.toast_label.adjustSize()
        self.position_toast()
        self.toast_label.show()
        self.toast_label.raise_()
        QTimer.singleShot(timeout_ms, self.toast_label.hide)

    def toast_style(self, kind: str) -> str:
        if kind == "warning":
            return (
                "background: #f97316; color: #fff7ed; border: 1px solid #fb923c; "
                "border-radius: 10px; padding: 12px 16px; font-weight: 800;"
            )
        if self.current_theme == "Light":
            return (
                "background: #ecfdf5; color: #065f46; border: 1px solid #34d399; "
                "border-radius: 10px; padding: 12px 16px; font-weight: 700;"
            )
        return (
            "background: #0f766e; color: #ecfeff; border: 1px solid #14b8a6; "
            "border-radius: 10px; padding: 12px 16px; font-weight: 700;"
        )

    def notify_export_done(self, label: str, path: str):
        filename = Path(path).name
        self.show_toast(f"{label} export completed: {filename}")

    def position_toast(self):
        if not hasattr(self, "toast_label"):
            return
        margin = 22
        x = max(margin, self.width() - self.toast_label.width() - margin)
        y = margin + self.menuBar().height()
        self.toast_label.move(x, y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_toast()

    def closeEvent(self, event):
        self.save_settings()
        super().closeEvent(event)

    # ---------------- Map ----------------

    def init_map(self):
        self.last_geojson_features = []
        self.update_map_status(0, 0)
        self.draw_features_on_map([])

    def on_basemap_changed(self):
        self.draw_features_on_map(self.last_geojson_features)

    def on_map_style_changed(self, style_name: str):
        if style_name not in self.map_style_presets():
            return
        self.map_style_preset = style_name
        self.draw_features_on_map(self.last_geojson_features)
        self.save_settings()
        self.statusBar().showMessage(f"Map style: {style_name}")

    def enable_map_area_drawing(self):
        if not WEBENGINE_AVAILABLE or self.map_view is None:
            QMessageBox.information(self, "Map unavailable", "QtWebEngine is required to draw on the map.")
            return
        self.statusBar().showMessage("Draw spatial filter: drag a rectangle on the map")
        self.map_view.page().runJavaScript("if (window.enableAreaDrawing) window.enableAreaDrawing();")

    def enable_map_polygon_drawing(self):
        if not WEBENGINE_AVAILABLE or self.map_view is None:
            QMessageBox.information(self, "Map unavailable", "QtWebEngine is required to draw on the map.")
            return
        self.statusBar().showMessage("Draw polygon filter: click vertices, double-click to finish")
        self.map_view.page().runJavaScript("if (window.enablePolygonDrawing) window.enablePolygonDrawing();")

    def on_map_area_drawn(self, west: float, south: float, east: float, north: float):
        min_lon, max_lon = sorted([float(west), float(east)])
        min_lat, max_lat = sorted([float(south), float(north)])
        if abs(max_lon - min_lon) < 1e-10 or abs(max_lat - min_lat) < 1e-10:
            QMessageBox.warning(self, "Invalid area", "Draw a larger area for the spatial filter.")
            return

        ring = [
            [min_lon, min_lat],
            [max_lon, min_lat],
            [max_lon, max_lat],
            [min_lon, max_lat],
            [min_lon, min_lat],
        ]
        geojson_geometry = {"type": "Polygon", "coordinates": [ring]}
        self.apply_geojson_spatial_filter(geojson_geometry, "Map drawn area")
        self.spatial_filter_label.setText(
            f"Spatial filter: drawn area / WGS84 ({min_lon:.5f}, {min_lat:.5f}) - ({max_lon:.5f}, {max_lat:.5f})"
        )
        self.statusBar().showMessage("Map area spatial filter applied; Geometry Lab updated")

    def on_map_polygon_drawn(self, coordinates_json: str):
        try:
            coordinates = json.loads(coordinates_json)
            ring = [
                [float(point[0]), float(point[1])]
                for point in coordinates
                if isinstance(point, list) and len(point) >= 2
            ]
        except Exception:
            QMessageBox.warning(self, "Invalid polygon", "Could not read polygon coordinates from map.")
            return

        if len(ring) < 3:
            QMessageBox.warning(self, "Invalid polygon", "Draw at least 3 vertices for a polygon filter.")
            return
        if ring[0] != ring[-1]:
            ring.append(ring[0])

        geojson_geometry = {"type": "Polygon", "coordinates": [ring]}
        try:
            self.validate_geojson_geometry(geojson_geometry)
        except Exception as exc:
            QMessageBox.warning(self, "Invalid polygon", str(exc))
            return

        self.apply_geojson_spatial_filter(geojson_geometry, "Map drawn polygon")
        self.statusBar().showMessage("Map polygon spatial filter applied; Geometry Lab updated")

    def apply_geojson_spatial_filter(self, geojson_geometry: dict[str, Any], source: str):
        self.spatial_filter_geometry, self.spatial_filter_geometry_type = geom_utils.geojson_geometry_to_arcgis(geojson_geometry)
        self.spatial_filter_geojson_feature = {
            "type": "Feature",
            "geometry": geojson_geometry,
            "properties": {
                "__featureIndex": -1,
                "__spatialFilter": True,
                "source": source,
                "geometryType": self.spatial_filter_geometry_type,
            },
        }
        self.use_spatial_filter.setChecked(True)
        self.spatial_filter_label.setText(f"Spatial filter: {source} / WGS84")
        self.write_spatial_filter_to_geometry_lab()
        self.draw_features_on_map(self.last_geojson_features)
        self.update_map_status(len(self.last_geojson_features), len(self.last_geojson_features))

    def write_spatial_filter_to_geometry_lab(self):
        if not self.spatial_filter_geometry or not self.spatial_filter_geometry_type:
            return

        payload = {
            "geometry": self.spatial_filter_geometry,
            "geometryType": self.spatial_filter_geometry_type,
        }
        self.geometry_lab_format_combo.setCurrentText("ArcGIS JSON")
        self.geometry_lab_input_sr_combo.setCurrentText("Auto")
        self.geometry_lab_input.setPlainText(json.dumps(payload, indent=2, ensure_ascii=False))

    def get_basemap_config(self) -> dict[str, Any]:
        config = dict(BASEMAPS.get(self.basemap_combo.currentText(), BASEMAPS["OpenStreetMap"]))
        if config.get("provider") == "google":
            config["googleApiKey"] = self.google_maps_api_key
        return config

    def build_map_html(self, geojson_features: list[dict[str, Any]]) -> str:
        return build_leaflet_map_html(
            geojson_features,
            self.get_basemap_config(),
            self.get_leaflet_style_from_arcgis_renderer(),
            self.current_theme,
        )

    def get_leaflet_style_from_arcgis_renderer(self) -> dict[str, Any]:
        return build_leaflet_style_from_renderer(self.current_layer_metadata, self.map_style_preset)

    @staticmethod
    def arcgis_color_to_hex(color: list[int]) -> str:
        return arcgis_color_to_hex(color)

    # ---------------- HTTP / Connection ----------------

    def get_token(self) -> str:
        return self.token_input.text().strip()

    def default_auth_config(self) -> dict[str, Any]:
        return {
            "mode": "server",
            "token_url": "",
            "portal_url": "",
            "server_url": "",
            "client": "referer",
            "referer": "arcgis-rest-explorer",
            "ip": "",
            "expiration": 60,
        }

    def normalize_auth_config(self, auth_config: dict[str, Any] | None, services_url: str = "") -> dict[str, Any]:
        config = self.default_auth_config()
        if isinstance(auth_config, dict):
            config.update({k: v for k, v in auth_config.items() if v is not None})
        if not config.get("token_url") and services_url:
            config["token_url"] = GenerateTokenDialog.default_token_url(services_url, config)
        return config

    def auth_config_for_storage(self, auth_config: dict[str, Any] | None = None) -> dict[str, Any]:
        config = dict(auth_config or self.current_auth_config)
        config.pop("username", None)
        config.pop("password", None)
        return config

    def add_token(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        token = self.get_token()
        if token:
            params["token"] = token
        return params

    def normalize_services_url(self) -> str:
        url = self.base_url.text().strip().rstrip("/")
        if not url.endswith("/rest/services") and "/rest/services" not in url:
            url = url.rstrip("/") + "/rest/services"
        return url

    def set_busy(self, busy: bool):
        self.connect_btn.setEnabled(not busy)
        self.query_btn.setEnabled(not busy)
        if hasattr(self, "run_operation_btn"):
            self.run_operation_btn.setEnabled(not busy and self.operation_combo.count() > 0)
        if hasattr(self, "operation_combo"):
            self.operation_combo.setEnabled(not busy)
        self.fetch_all_pages.setEnabled(not busy)
        self.draw_area_filter_btn.setEnabled(not busy)
        self.draw_polygon_filter_btn.setEnabled(not busy)
        self.geometry_lab_import_btn.setEnabled(not busy)
        self.geometry_lab_export_btn.setEnabled(not busy)
        self.geometry_lab_preview_btn.setEnabled(not busy)
        self.geometry_lab_use_btn.setEnabled(not busy)
        self.clear_token_btn.setEnabled(not busy)
        self.forget_token_btn.setEnabled(not busy)
        self.auth_settings_btn.setEnabled(not busy)
        self.generate_token_btn.setEnabled(not busy)
        self.add_condition_btn.setEnabled(not busy)
        self.apply_where_btn.setEnabled(not busy)
        self.reset_where_btn.setEnabled(not busy)
        self.save_conn_btn.setEnabled(not busy)
        self.delete_conn_btn.setEnabled(not busy)
        if hasattr(self, "save_query_btn"):
            self.save_query_btn.setEnabled(not busy)
            self.load_query_btn.setEnabled(not busy)
            self.delete_query_btn.setEnabled(not busy)
        self.update_stop_query_button()
        self.statusBar().showMessage("Loading..." if busy else "Ready")

    def is_parallel_fetch_running(self) -> bool:
        return (
            self.fetch_all_worker is not None
            and self.fetch_all_worker.isRunning()
            and self.fetch_all_request_id == self.active_request_id
        )

    def is_gp_job_running(self) -> bool:
        return (
            self.gp_job_worker is not None
            and self.gp_job_worker.isRunning()
            and self.gp_job_request_id == self.active_request_id
        )

    def is_standard_request_running(self) -> bool:
        worker = self.workers.get(self.active_request_id)
        return worker is not None and worker.isRunning()

    def is_request_running(self) -> bool:
        return self.is_parallel_fetch_running() or self.is_standard_request_running() or self.is_gp_job_running()

    def update_stop_query_button(self):
        if not hasattr(self, "stop_query_btn"):
            return
        running = self.is_request_running()
        parallel_stopping = (
            self.is_parallel_fetch_running()
            and self.fetch_all_worker.isInterruptionRequested()
        )
        self.stop_query_btn.setVisible(running)
        self.stop_query_btn.setEnabled(running and not parallel_stopping)

    def update_parallel_fetch_stop_button(self):
        self.update_stop_query_button()

    def _get_json(self, url: str, callback, params: dict[str, Any] | None = None):
        self.set_busy(True)
        request_params = self.add_token(params)
        self.request_counter += 1
        request_id = self.request_counter
        self.active_request_id = request_id
        try:
            self.last_request_url = str(httpx.URL(url, params=request_params))
        except Exception:
            self.last_request_url = url
        logger.info("Request %s: %s", request_id, self.redact_token_from_url(self.last_request_url))
        self.worker = HttpWorker(url, request_params, self.http_read_timeout_seconds, verify_ssl=self.verify_ssl)
        self.workers[request_id] = self.worker
        self.worker.ok.connect(lambda data, elapsed_ms, rid=request_id: self._on_ok(data, elapsed_ms, callback, rid))
        self.worker.fail.connect(lambda message, rid=request_id: self._on_fail(message, rid))
        self.worker.finished.connect(lambda rid=request_id: self.workers.pop(rid, None))
        self.worker.start()
        self.update_stop_query_button()

    def _on_ok(self, data: object, elapsed_ms: float, callback, request_id: int):
        if request_id != self.active_request_id:
            logger.info("Ignoring stale response %s", request_id)
            return
        self.active_request_id = 0
        self.set_busy(False)
        self.last_response = data
        self.last_request_elapsed_ms = elapsed_ms
        self.statusBar().showMessage(f"Ready - last request {elapsed_ms:.0f} ms")
        self.add_history_entry(elapsed_ms)
        callback(data)

    def _on_fail(self, message: str, request_id: int):
        if request_id != self.active_request_id:
            logger.info("Ignoring stale failure %s: %s", request_id, message)
            return
        self.active_request_id = 0
        self.set_busy(False)
        logger.error("Request %s failed: %s", request_id, message)
        QMessageBox.critical(self, "Request error", message)
        self.statusBar().showMessage("Error")

    def load_connections(self):
        self.connections = []
        if CONNECTIONS_FILE.exists():
            data, error = load_json_file(CONNECTIONS_FILE, [])
            if error:
                backup_path = backup_corrupt_json(CONNECTIONS_FILE)
                logger.warning("%s Backup: %s", error, backup_path)
                QMessageBox.warning(self, "Connections file error", f"{error}\nBackup: {backup_path}")
            elif isinstance(data, list):
                self.connections = data
            else:
                QMessageBox.warning(self, "Connections file error", "connections.json must contain a list.")
        self.migrate_legacy_connection_tokens()
        if not KEYRING_AVAILABLE:
            self.statusBar().showMessage("Keyring unavailable: tokens are session-only unless already saved externally")
        self.refresh_connection_combo()

    def migrate_legacy_connection_tokens(self):
        changed = False
        migrated = 0
        for conn in self.connections:
            if not isinstance(conn, dict):
                continue
            name = conn.get("name", "")
            if "auth" not in conn:
                conn["auth"] = self.normalize_auth_config(None, conn.get("url", ""))
                changed = True
            legacy_token = conn.pop("token", "")
            if legacy_token:
                changed = True
                if KEYRING_AVAILABLE and save_token(name, legacy_token):
                    conn["token_storage"] = "keyring"
                    migrated += 1
                else:
                    conn["token_storage"] = "session_only"
            if "token" in conn:
                conn.pop("token", None)
                changed = True
        if changed:
            atomic_write_json(CONNECTIONS_FILE, self.connections)
            logger.info("Migrated %s legacy tokens to keyring", migrated)
            if migrated:
                self.statusBar().showMessage(f"Legacy tokens migrated to keyring: {migrated}")

    def refresh_connection_combo(self):
        self.connection_combo.blockSignals(True)
        self.connection_combo.clear()
        self.connection_combo.addItem("-- new connection --")
        for conn in self.connections:
            self.connection_combo.addItem(conn.get("name", "Unnamed"))
        self.connection_combo.blockSignals(False)

    def on_connection_selected(self, index: int):
        if index <= 0:
            self.current_auth_config = self.normalize_auth_config(None, self.base_url.text().strip())
            self.update_auth_status()
            return
        conn = self.connections[index - 1]
        name = conn.get("name", "")
        token = get_saved_token(name)
        self.current_auth_config = self.normalize_auth_config(conn.get("auth"), conn.get("url", ""))
        self.connection_name.setText(conn.get("name", ""))
        self.base_url.setText(conn.get("url", ""))
        self.token_input.setText(token)
        self.token_expiry_label.setText(conn.get("token_expires", "Token: not checked"))
        self.update_auth_status(conn.get("token_storage", "keyring" if token else "none"))

    def save_current_connection(self):
        name = self.connection_name.text().strip()
        url = self.base_url.text().strip()
        token = self.token_input.text().strip()
        token_expires = self.token_expiry_label.text()
        if not name or not url:
            QMessageBox.warning(self, "Missing data", "Insert connection name and REST services URL.")
            return
        existing = next((c for c in self.connections if c.get("name") == name), None)
        token_saved = save_token(name, token) if token else False
        if not token:
            delete_token(name)
        token_storage = "keyring" if token_saved else ("session_only" if token else "none")
        self.current_auth_config = self.normalize_auth_config(self.current_auth_config, url)
        payload = {
            "name": name,
            "url": url,
            "token_expires": token_expires,
            "token_storage": token_storage,
            "auth": self.auth_config_for_storage(),
        }
        if existing:
            existing.update(payload)
            existing.pop("token", None)
        else:
            self.connections.append(payload)
        atomic_write_json(CONNECTIONS_FILE, self.connections)
        self.refresh_connection_combo()
        self.update_auth_status(token_storage)
        if token and not token_saved:
            QMessageBox.warning(
                self,
                "Token not persisted",
                "Connection saved, but the token was not saved because keyring is unavailable or rejected the write.",
            )
        self.statusBar().showMessage(f"Connection saved: {name}")

    def delete_current_connection(self):
        name = self.connection_name.text().strip()
        if not name:
            return
        self.connections = [c for c in self.connections if c.get("name") != name]
        delete_token(name)
        delete_credentials(name)
        atomic_write_json(CONNECTIONS_FILE, self.connections)
        self.connection_name.clear()
        self.token_input.clear()
        self.token_expiry_label.setText("Token: not checked")
        self.current_auth_config = self.default_auth_config()
        self.refresh_connection_combo()
        self.update_auth_status("none")
        self.statusBar().showMessage(f"Connection deleted: {name}")

    def open_connection_auth_settings(self):
        url = self.base_url.text().strip()
        self.current_auth_config = self.normalize_auth_config(self.current_auth_config, url)
        dialog = ConnectionAuthDialog(url, self.current_auth_config, self)
        if dialog.exec() != QDialog.Accepted:
            return
        self.current_auth_config = self.normalize_auth_config(dialog.get_auth_config(), url)
        self.update_auth_status("keyring" if self.get_token() else "none")
        self.statusBar().showMessage(f"Auth settings updated: {self.current_auth_config.get('mode')}")

    # ---------------- Catalog ----------------

    def load_services_root(self):
        self.tree.clear()
        self.current_layer_url = None
        self.current_layer_metadata = None
        self.current_layer_metadata_url = None
        self.update_operation_panel(None, None, None)
        self.table.clear()
        self.metadata_text.clear()
        self.query_builder_fields = []
        self.condition_table.setRowCount(0)
        self.where_preview.clear()
        self.init_map()
        self._get_json(self.normalize_services_url(), self.populate_services_root)

    def populate_services_root(self, data: dict[str, Any]):
        self.response_text.setPlainText(json.dumps(data, indent=2, ensure_ascii=False))
        root_url = self.normalize_services_url()

        for folder in data.get("folders", []):
            item = QTreeWidgetItem([folder])
            item.setData(0, Qt.UserRole, ArcGISNodeData("folder", f"{root_url}/{folder}", folder))
            item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            self.tree.addTopLevelItem(item)

        for service in data.get("services", []):
            name = service.get("name", "")
            service_type = service.get("type", "")
            url = f"{root_url}/{name}/{service_type}"
            item = QTreeWidgetItem([f"{name} ({service_type})"])
            item.setData(0, Qt.UserRole, ArcGISNodeData("service", url, name))
            item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            self.tree.addTopLevelItem(item)

        self.statusBar().showMessage("Services loaded")

    def on_item_expanded(self, item: QTreeWidgetItem):
        if item.childCount() > 0:
            return
        data = item.data(0, Qt.UserRole)
        if not isinstance(data, ArcGISNodeData):
            return
        if data.kind == "folder":
            self._get_json(data.url, lambda json_data: self.populate_folder(item, json_data))
        elif data.kind == "service":
            self._get_json(data.url, lambda json_data: self.populate_service(item, json_data, data.url))

    def populate_folder(self, parent: QTreeWidgetItem, data: dict[str, Any]):
        for service in data.get("services", []):
            name = service.get("name", "")
            service_type = service.get("type", "")
            short_name = name.split("/")[-1]
            url = f"{self.normalize_services_url()}/{name}/{service_type}"
            item = QTreeWidgetItem([f"{short_name} ({service_type})"])
            item.setData(0, Qt.UserRole, ArcGISNodeData("service", url, name))
            item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            parent.addChild(item)

    def populate_service(self, parent: QTreeWidgetItem, data: dict[str, Any], service_url: str):
        self.response_text.setPlainText(json.dumps(data, indent=2, ensure_ascii=False))

        if service_url.rstrip("/").endswith("/GPServer"):
            for task in data.get("tasks", []):
                task_name = task.get("name") if isinstance(task, dict) else str(task)
                if not task_name:
                    continue
                task_url = f"{service_url.rstrip('/')}/{quote(task_name, safe='')}"
                item = QTreeWidgetItem([f"{task_name} [GP Task]"])
                item.setData(0, Qt.UserRole, ArcGISNodeData("gp_task", task_url, task_name))
                parent.addChild(item)
            return

        for layer in data.get("layers", []):
            layer_id = layer.get("id")
            name = layer.get("name", f"Layer {layer_id}")
            geometry = layer.get("geometryType", "")
            url = f"{service_url}/{layer_id}"
            item = QTreeWidgetItem([f"{layer_id} - {name} {geometry}".strip()])
            item.setData(0, Qt.UserRole, ArcGISNodeData("layer", url, name))
            parent.addChild(item)

        for table in data.get("tables", []):
            table_id = table.get("id")
            name = table.get("name", f"Table {table_id}")
            url = f"{service_url}/{table_id}"
            item = QTreeWidgetItem([f"{table_id} - {name} [Table]"])
            item.setData(0, Qt.UserRole, ArcGISNodeData("layer", url, name))
            parent.addChild(item)

    def on_item_clicked(self, item: QTreeWidgetItem):
        data = item.data(0, Qt.UserRole)
        if isinstance(data, ArcGISNodeData) and data.kind == "layer":
            self.current_layer_url = data.url
            self._get_json(data.url, self.show_layer_metadata)
        elif isinstance(data, ArcGISNodeData) and data.kind == "gp_task":
            self.current_layer_url = None
            self.current_layer_metadata = None
            self.current_layer_metadata_url = None
            self._get_json(data.url, lambda json_data, node=data: self.show_gp_task_metadata(node, json_data))

    def open_catalog_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.UserRole)
        if not isinstance(data, ArcGISNodeData) or data.kind != "layer":
            return

        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        metadata_action = menu.addAction("Show Layer Metadata")
        action = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if action == metadata_action:
            self.current_layer_url = data.url
            if self.current_layer_metadata and self.current_layer_metadata_url == data.url:
                self.show_layer_metadata_popup(self.current_layer_metadata)
            else:
                self._get_json(data.url, lambda json_data: self.show_layer_metadata(json_data, show_popup=True))

    # ---------------- Metadata / Query Builder ----------------

    def get_field_aliases(self) -> dict[str, str]:
        aliases = {}
        meta = self.current_layer_metadata or {}
        for f in meta.get("fields", []):
            name = f.get("name")
            alias = f.get("alias")
            if name and alias:
                aliases[name] = alias
        return aliases

    def get_field_by_name(self, name: str) -> dict[str, Any] | None:
        meta = self.current_layer_metadata or {}
        for f in meta.get("fields", []):
            if f.get("name") == name:
                return f
        return None

    def get_object_id_field(self) -> str:
        meta = self.current_layer_metadata or {}
        return meta.get("objectIdField") or "OBJECTID"

    def build_layer_metadata_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        fields = data.get("fields", [])
        domain_count = 0
        for f in fields:
            domain = f.get("domain")
            if isinstance(domain, dict) and isinstance(domain.get("codedValues"), list):
                domain_count += 1

        renderer = data.get("drawingInfo", {}).get("renderer", {})
        return {
            "name": data.get("name"),
            "type": data.get("type"),
            "geometryType": data.get("geometryType"),
            "objectIdField": data.get("objectIdField"),
            "maxRecordCount": data.get("maxRecordCount"),
            "fields": len(fields),
            "domainFields": domain_count,
            "rendererType": renderer.get("type") if isinstance(renderer, dict) else None,
            "supportsPagination": data.get("advancedQueryCapabilities", {}).get("supportsPagination"),
            "supportsOrderBy": data.get("advancedQueryCapabilities", {}).get("supportsOrderBy"),
        }

    def show_layer_metadata(self, data: dict[str, Any], show_popup: bool = False):
        self.current_layer_metadata = data
        self.current_layer_metadata_url = self.current_layer_url
        self.update_operation_panel("layer", self.current_layer_url, data)
        self.populate_query_builder_fields()

        useful = self.build_layer_metadata_summary(data)
        self.metadata_text.setPlainText(json.dumps(useful, ensure_ascii=False, separators=(",", ":")))
        self.response_text.setPlainText(json.dumps(data, indent=2, ensure_ascii=False))
        self.update_map_status(0, 0)
        self.statusBar().showMessage(f"Layer selected: {data.get('name', '')}")
        if show_popup:
            self.show_layer_metadata_popup(data)

    def show_gp_task_metadata(self, node: ArcGISNodeData, data: dict[str, Any]):
        self.current_operation_url = node.url
        self.current_operation_kind = "gp_task"
        self.current_operation_metadata = data if isinstance(data, dict) else {}
        self.update_operation_panel("gp_task", node.url, self.current_operation_metadata)
        self.response_text.setPlainText(json.dumps(data, indent=2, ensure_ascii=False))
        self.metadata_text.setPlainText(json.dumps(self.build_gp_task_summary(self.current_operation_metadata), ensure_ascii=False, separators=(",", ":")))
        self.statusBar().showMessage(f"GP task selected: {node.name}")

    def build_gp_task_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        return build_gp_task_summary(data)

    def show_layer_metadata_popup(self, data: dict[str, Any]):
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Layer Metadata - {data.get('name', 'Layer')}")
        dialog.resize(950, 720)

        layout = QVBoxLayout(dialog)
        summary = QTextEdit()
        summary.setReadOnly(True)
        summary.setMaximumHeight(140)
        summary.setPlainText(json.dumps(self.build_layer_metadata_summary(data), indent=2, ensure_ascii=False))

        full_json = QTextEdit()
        full_json.setReadOnly(True)
        full_json.setPlainText(json.dumps(data, indent=2, ensure_ascii=False))

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)

        layout.addWidget(QLabel("Summary"))
        layout.addWidget(summary)
        layout.addWidget(QLabel("Full metadata"))
        layout.addWidget(full_json, 1)
        layout.addWidget(buttons)
        dialog.exec()


    def populate_query_builder_fields(self):
        self.query_builder_fields = []
        meta = self.current_layer_metadata or {}
        for f in meta.get("fields", []):
            name = f.get("name")
            alias = f.get("alias")
            f_type = f.get("type", "")
            if not name:
                continue
            label = f"{name} - {alias}" if alias and alias != name else name
            self.query_builder_fields.append({"label": label, "name": name, "type": f_type, "field": f})

        self.reset_visual_builder(update_where=False)

    def add_condition_row(self):
        row = self.condition_table.rowCount()
        self.condition_table.insertRow(row)

        join_combo = QComboBox()
        join_combo.addItems(["AND", "OR"])
        join_combo.setEnabled(row > 0)
        join_combo.currentTextChanged.connect(lambda *_args: self.update_query_preview())

        field_combo = QComboBox()
        for field in self.query_builder_fields:
            field_combo.addItem(field["label"], field)
        field_combo.currentIndexChanged.connect(lambda *_args, combo=field_combo: self.on_condition_field_changed(combo))

        operator_combo = QComboBox()
        operator_combo.addItems(["=", "<>", ">", ">=", "<", "<=", "LIKE", "IS NULL", "IS NOT NULL"])
        operator_combo.currentTextChanged.connect(lambda *_args, combo=operator_combo: self.on_condition_operator_changed(combo))

        value_combo = QComboBox()
        value_combo.setEditable(True)
        value_combo.setInsertPolicy(QComboBox.NoInsert)
        value_combo.currentTextChanged.connect(lambda *_args: self.update_query_preview())

        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(lambda *_args, button=remove_btn: self.remove_condition_row_by_button(button))

        self.condition_table.setCellWidget(row, 0, join_combo)
        self.condition_table.setCellWidget(row, 1, field_combo)
        self.condition_table.setCellWidget(row, 2, operator_combo)
        self.condition_table.setCellWidget(row, 3, value_combo)
        self.condition_table.setCellWidget(row, 4, remove_btn)
        self.on_condition_field_changed(field_combo)
        self.update_query_preview()

    def remove_condition_row_by_button(self, button: QPushButton):
        for row in range(self.condition_table.rowCount()):
            if self.condition_table.cellWidget(row, 4) is button:
                self.condition_table.removeRow(row)
                break
        self.reindex_condition_rows()
        self.update_query_preview()

    def reindex_condition_rows(self):
        for row in range(self.condition_table.rowCount()):
            join_combo = self.condition_table.cellWidget(row, 0)
            if isinstance(join_combo, QComboBox):
                join_combo.setEnabled(row > 0)

    def on_condition_field_changed(self, field_combo: QComboBox):
        row = self.find_condition_row(field_combo)
        if row is None:
            return
        value_combo = self.condition_table.cellWidget(row, 3)
        if not isinstance(value_combo, QComboBox):
            return

        value_combo.blockSignals(True)
        value_combo.clear()
        value_combo.setEditable(True)

        data = field_combo.currentData()
        field = data.get("field", {}) if isinstance(data, dict) else {}
        domain = field.get("domain")
        if isinstance(domain, dict):
            coded_values = domain.get("codedValues")
            if isinstance(coded_values, list):
                value_combo.setEditable(False)
                for cv in coded_values:
                    code = cv.get("code")
                    name = cv.get("name")
                    value_combo.addItem(f"{name} ({code})", code)
        value_combo.blockSignals(False)
        self.update_query_preview()

    def on_condition_operator_changed(self, operator_combo: QComboBox):
        row = self.find_condition_row(operator_combo)
        if row is None:
            return
        value_combo = self.condition_table.cellWidget(row, 3)
        if isinstance(value_combo, QComboBox):
            value_combo.setEnabled(operator_combo.currentText() not in ("IS NULL", "IS NOT NULL"))
        self.update_query_preview()

    def find_condition_row(self, widget: QWidget) -> int | None:
        for row in range(self.condition_table.rowCount()):
            for col in range(self.condition_table.columnCount()):
                if self.condition_table.cellWidget(row, col) is widget:
                    return row
        return None

    def build_visual_where(self) -> str:
        parts = []
        for row in range(self.condition_table.rowCount()):
            condition = self.build_condition_from_row(row)
            if not condition:
                continue
            join_combo = self.condition_table.cellWidget(row, 0)
            joiner = join_combo.currentText() if isinstance(join_combo, QComboBox) and parts else ""
            parts.append((joiner, condition))

        if not parts:
            return "1=1"

        sql = parts[0][1]
        for joiner, condition in parts[1:]:
            sql = f"({sql}) {joiner} ({condition})"
        return sql

    def build_condition_from_row(self, row: int) -> str:
        field_combo = self.condition_table.cellWidget(row, 1)
        operator_combo = self.condition_table.cellWidget(row, 2)
        value_combo = self.condition_table.cellWidget(row, 3)
        if not isinstance(field_combo, QComboBox) or not isinstance(operator_combo, QComboBox):
            return ""

        data = field_combo.currentData()
        if not isinstance(data, dict):
            return ""

        field_name = data["name"]
        field_type = data.get("type", "")
        op = operator_combo.currentText()
        if op in ("IS NULL", "IS NOT NULL"):
            return f"{field_name} {op}"

        if not isinstance(value_combo, QComboBox):
            return ""
        raw_value = value_combo.currentData()
        if raw_value is None:
            raw_value = value_combo.currentText().strip()
        if raw_value == "":
            return ""

        value = self.format_sql_value(raw_value, field_type, op)
        return f"{field_name} {op} {value}"

    def update_query_preview(self):
        if not hasattr(self, "where_preview"):
            return
        self.where_preview.setPlainText(self.build_visual_where())

    def apply_visual_where(self):
        self.where_input.setText(self.build_visual_where())

    def reset_visual_builder(self, update_where: bool = True):
        self.condition_table.setRowCount(0)
        if self.query_builder_fields:
            self.add_condition_row()
        self.update_query_preview()
        if update_where:
            self.where_input.setText("1=1")

    def format_sql_value(self, value: Any, field_type: str, op: str) -> str:
        if value is None:
            return "NULL"

        numeric_types = {
            "esriFieldTypeOID",
            "esriFieldTypeInteger",
            "esriFieldTypeSmallInteger",
            "esriFieldTypeDouble",
            "esriFieldTypeSingle",
        }

        if field_type in numeric_types:
            return str(value)

        text = str(value)
        text = text.replace("'", "''")

        if field_type == "esriFieldTypeDate":
            return f"timestamp '{text}'"

        if op == "LIKE" and "%" not in text:
            text = f"%{text}%"

        return f"'{text}'"

    # ---------------- REST Operations / GP Jobs ----------------

    def update_operation_panel(self, kind: str | None, url: str | None, metadata: dict[str, Any] | None):
        if not hasattr(self, "operation_combo"):
            return
        self.current_operation_kind = kind
        self.current_operation_url = url
        self.current_operation_metadata = metadata or {}
        self.operation_combo.blockSignals(True)
        self.operation_combo.clear()
        self.operation_params.clear()
        self.operation_status.clear()
        if not kind or not url:
            self.operation_target_label.setText("No REST operation target selected")
            self.operation_output.setPlainText("Select a FeatureServer/MapServer layer or a GPServer task from the catalog.")
            self.run_operation_btn.setEnabled(False)
            self.operation_combo.blockSignals(False)
            return

        self.operation_target_label.setText(url)
        operations = self.operation_definitions(kind, url, self.current_operation_metadata)
        for operation in operations:
            self.operation_combo.addItem(operation["label"], operation)
        self.operation_combo.blockSignals(False)
        self.operation_combo.setEnabled(bool(operations))
        self.run_operation_btn.setEnabled(bool(operations))
        self.on_operation_changed()

    def operation_definitions(self, kind: str, url: str, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        if kind == "gp_task":
            return self.gp_operation_definitions(metadata)
        if kind == "layer":
            return self.layer_operation_definitions(url, metadata)
        return []

    def layer_operation_definitions(self, url: str, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        return layer_operation_definitions(url, metadata)

    def gp_operation_definitions(self, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        return gp_operation_definitions(metadata)

    def default_gp_input_params(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return default_gp_input_params(metadata)

    def on_operation_changed(self):
        if not hasattr(self, "operation_combo"):
            return
        operation = self.operation_combo.currentData()
        if not isinstance(operation, dict):
            return
        self.operation_params.setPlainText(json.dumps(operation.get("params", {}), indent=2, ensure_ascii=False))
        if operation.get("mode") == "gp_submit":
            self.operation_status.setPlainText("submitJob will start an async GP job and poll /jobs/<jobId> until a final status.")
        else:
            self.operation_status.setPlainText(f"Ready to call /{operation.get('endpoint')}.")

    def read_operation_params(self) -> dict[str, Any] | None:
        text = self.operation_params.toPlainText().strip()
        if not text:
            params: dict[str, Any] = {}
        else:
            try:
                params = json.loads(text)
            except Exception as exc:
                QMessageBox.warning(self, "Invalid operation parameters", f"Parameters must be a JSON object.\n{exc}")
                return None
            if not isinstance(params, dict):
                QMessageBox.warning(self, "Invalid operation parameters", "Parameters must be a JSON object.")
                return None
        params.setdefault("f", "json")
        return params

    def normalize_operation_params(self, params: dict[str, Any]) -> dict[str, Any] | None:
        if self.current_operation_kind != "gp_task":
            return params
        try:
            normalized = normalize_gp_input_params(self.current_operation_metadata, params)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid GP parameters", str(exc))
            return None
        if normalized != params:
            self.operation_params.setPlainText(json.dumps(normalized, indent=2, ensure_ascii=False))
        return normalized

    def run_selected_operation(self):
        if not self.current_operation_url:
            QMessageBox.warning(self, "No operation target", "Select a layer or GP task from the catalog first.")
            return
        operation = self.operation_combo.currentData()
        if not isinstance(operation, dict):
            QMessageBox.warning(self, "No operation selected", "Select an operation first.")
            return
        params = self.read_operation_params()
        if params is None:
            return
        params = self.normalize_operation_params(params)
        if params is None:
            return

        if operation.get("mode") == "gp_submit":
            self.start_gp_job(self.current_operation_url, params)
            return

        endpoint = str(operation.get("endpoint", "")).strip("/")
        operation_url = f"{self.current_operation_url.rstrip('/')}/{endpoint}" if endpoint else self.current_operation_url
        self.operation_status.setPlainText(f"Calling {operation_url}")
        self._get_json(operation_url, self.show_operation_result, params=params)

    def show_operation_result(self, data: object):
        text = json.dumps(data, indent=2, ensure_ascii=False)
        self.operation_output.setPlainText(text)
        self.response_text.setPlainText(text)
        self.statusBar().showMessage("Operation completed")

    def start_gp_job(self, task_url: str, params: dict[str, Any]):
        self.set_busy(True)
        request_params = self.add_token(params)
        self.request_counter += 1
        request_id = self.request_counter
        self.active_request_id = request_id
        self.gp_job_request_id = request_id
        try:
            self.last_request_url = str(httpx.URL(f"{task_url.rstrip('/')}/submitJob", params=request_params))
        except Exception:
            self.last_request_url = f"{task_url.rstrip('/')}/submitJob"
        logger.info("GP submitJob %s: %s", request_id, self.redact_token_from_url(self.last_request_url))

        self.gp_job_worker = GpJobWorker(
            task_url,
            request_params,
            read_timeout_seconds=self.http_read_timeout_seconds,
            verify_ssl=self.verify_ssl,
        )
        self.gp_job_worker.status.connect(lambda status, data, rid=request_id: self.on_gp_job_status(status, data, rid))
        self.gp_job_worker.ok.connect(lambda data, elapsed_ms, rid=request_id: self.on_gp_job_ok(data, elapsed_ms, rid))
        self.gp_job_worker.fail.connect(lambda message, rid=request_id: self.on_gp_job_fail(message, rid))
        self.gp_job_worker.cancelled.connect(lambda rid=request_id: self.on_gp_job_cancelled(rid))
        self.gp_job_worker.finished.connect(lambda rid=request_id: self.on_gp_job_finished(rid))
        self.operation_status.setPlainText("Submitting GP job...")
        self.gp_job_worker.start()
        self.update_stop_query_button()

    def on_gp_job_status(self, status: str, data: object, request_id: int):
        if request_id != self.active_request_id:
            return
        self.operation_status.setPlainText(f"GP job status: {status}")
        self.operation_output.setPlainText(json.dumps(data, indent=2, ensure_ascii=False))
        self.statusBar().showMessage(f"GP job status: {status}")

    def on_gp_job_ok(self, data: object, elapsed_ms: float, request_id: int):
        if request_id != self.active_request_id:
            return
        self.active_request_id = 0
        self.set_busy(False)
        self.last_response = data
        self.last_request_elapsed_ms = elapsed_ms
        self.add_history_entry(elapsed_ms)
        text = json.dumps(data, indent=2, ensure_ascii=False)
        self.operation_output.setPlainText(text)
        self.response_text.setPlainText(text)
        status = data.get("jobStatus", "completed") if isinstance(data, dict) else "completed"
        self.operation_status.setPlainText(f"GP job final status: {status}")
        self.statusBar().showMessage(f"GP job completed in {elapsed_ms:.0f} ms: {status}")

    def on_gp_job_fail(self, message: str, request_id: int):
        if request_id != self.active_request_id:
            return
        self.active_request_id = 0
        self.set_busy(False)
        logger.error("GP job %s failed: %s", request_id, message)
        self.operation_status.setPlainText(f"GP job error: {message}")
        QMessageBox.critical(self, "GP job error", message)
        self.statusBar().showMessage("GP job error")

    def on_gp_job_cancelled(self, request_id: int):
        if request_id != self.active_request_id:
            return
        self.active_request_id = 0
        self.set_busy(False)
        self.operation_status.setPlainText("GP job polling stopped")
        self.statusBar().showMessage("GP job polling stopped")

    def on_gp_job_finished(self, request_id: int):
        if request_id == self.gp_job_request_id:
            self.gp_job_worker = None
            self.gp_job_request_id = 0
        self.update_stop_query_button()

    # ---------------- Query / Results ----------------

    def run_query(self):
        if not self.current_layer_url:
            QMessageBox.warning(self, "No layer selected", "Select a FeatureServer/MapServer layer first.")
            return

        params = build_query_params(
            self.where_input.text(),
            self.out_fields.text(),
            self.return_geometry.isChecked(),
            self.max_records.currentText(),
            self.order_by.text(),
            self.spatial_filter_geometry if self.use_spatial_filter.isChecked() else None,
            self.spatial_filter_geometry_type if self.use_spatial_filter.isChecked() else None,
            self.geometry_lab_spatial_rel_combo.currentText(),
        )

        query_url = self.current_layer_url.rstrip("/") + "/query"
        self.last_query_out_wkid = self.parse_wkid(params.get("outSR"))
        if self.fetch_all_pages.isChecked():
            if not self.current_layer_metadata or not self.current_layer_metadata.get("advancedQueryCapabilities", {}).get("supportsPagination"):
                QMessageBox.warning(self, "Pagination unavailable", "This layer does not advertise pagination support.")
                return
            self.start_parallel_fetch_all(query_url, params)
            return

        self._get_json(query_url, self.show_query_result, params=params)

    def start_parallel_fetch_all(self, query_url: str, params: dict[str, Any]):
        self.set_busy(True)
        request_params = self.add_token(params)
        self.request_counter += 1
        request_id = self.request_counter
        self.active_request_id = request_id
        self.fetch_all_request_id = request_id

        try:
            self.last_request_url = str(httpx.URL(query_url, params=request_params))
        except Exception:
            self.last_request_url = query_url

        logger.info("Parallel fetch all %s: %s", request_id, self.redact_token_from_url(self.last_request_url))
        self.fetch_all_worker = FetchAllWorker(
            query_url,
            request_params,
            int(self.max_records.currentText()),
            max_workers=4,
            read_timeout_seconds=self.http_read_timeout_seconds,
            verify_ssl=self.verify_ssl,
        )
        self.fetch_all_worker.progress.connect(
            lambda completed, total, rid=request_id: self.on_parallel_fetch_progress(completed, total, rid)
        )
        self.fetch_all_worker.ok.connect(
            lambda data, elapsed_ms, pages, rid=request_id: self.on_parallel_fetch_ok(data, elapsed_ms, pages, rid)
        )
        self.fetch_all_worker.fail.connect(
            lambda message, rid=request_id: self.on_parallel_fetch_fail(message, rid)
        )
        self.fetch_all_worker.cancelled.connect(
            lambda rid=request_id: self.on_parallel_fetch_cancelled(rid)
        )
        self.fetch_all_worker.finished.connect(
            lambda rid=request_id: self.on_parallel_fetch_finished(rid)
        )
        self.statusBar().showMessage("Counting features for parallel fetch...")
        self.fetch_all_worker.start()
        self.update_parallel_fetch_stop_button()

    def stop_active_request(self):
        if not self.is_request_running():
            return
        stopped_request_id = self.active_request_id
        if (
            self.fetch_all_worker is not None
            and self.fetch_all_worker.isRunning()
            and self.fetch_all_request_id == stopped_request_id
        ):
            self.fetch_all_worker.requestInterruption()
        if (
            self.gp_job_worker is not None
            and self.gp_job_worker.isRunning()
            and self.gp_job_request_id == stopped_request_id
        ):
            self.gp_job_worker.requestInterruption()
        worker = self.workers.get(stopped_request_id)
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
        self.active_request_id = 0
        self.set_busy(False)
        self.update_stop_query_button()
        self.statusBar().showMessage("Query stopped")
        self.show_toast("Query stopped by user", kind="warning")

    def stop_parallel_fetch_all(self):
        self.stop_active_request()

    def on_parallel_fetch_progress(self, completed: int, total: int, request_id: int):
        if request_id != self.active_request_id:
            return
        self.statusBar().showMessage(f"Parallel fetch: page {completed}/{total}")

    def on_parallel_fetch_ok(self, data: dict[str, Any], elapsed_ms: float, pages: int, request_id: int):
        if request_id != self.active_request_id:
            logger.info("Ignoring stale parallel fetch response %s", request_id)
            return
        self.active_request_id = 0
        self.set_busy(False)
        self.last_response = data
        self.last_request_elapsed_ms = elapsed_ms
        self.add_history_entry(elapsed_ms)
        self.statusBar().showMessage(f"Parallel fetch completed: {pages} pages in {elapsed_ms:.0f} ms")
        self.show_query_result(data)

    def on_parallel_fetch_fail(self, message: str, request_id: int):
        if request_id != self.active_request_id:
            logger.info("Ignoring stale parallel fetch failure %s: %s", request_id, message)
            return
        self.active_request_id = 0
        self.set_busy(False)
        logger.error("Parallel fetch %s failed: %s", request_id, message)
        QMessageBox.critical(self, "Parallel fetch error", message)
        self.statusBar().showMessage("Parallel fetch error")

    def on_parallel_fetch_cancelled(self, request_id: int):
        if request_id != self.active_request_id:
            logger.info("Ignoring stale parallel fetch cancellation %s", request_id)
            return
        self.active_request_id = 0
        self.set_busy(False)
        self.statusBar().showMessage("Parallel fetch stopped")
        self.show_toast("Parallel fetch stopped", kind="warning")

    def on_parallel_fetch_finished(self, request_id: int):
        if request_id == self.fetch_all_request_id:
            self.fetch_all_worker = None
            self.fetch_all_request_id = 0
        self.update_parallel_fetch_stop_button()

    def show_query_result(self, data: dict[str, Any]):
        features = data.get("features", [])
        if not isinstance(features, list):
            features = []
        total_features = len(features)
        self.reset_query_render_state(features)
        self.response_text.setPlainText(self.build_response_text_preview(data, features))
        if not features:
            self.table.clear()
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            self.last_geojson_features = []
            self.draw_features_on_map([])
            self.update_map_status(0, 0)
            self.statusBar().showMessage("Query completed: 0 features")
            self.show_toast("Query completed: 0 features")
            return

        self.table.clear()
        self.query_table_columns = self.initial_table_columns(features)
        self.table.setColumnCount(len(self.query_table_columns))
        self.table.setHorizontalHeaderLabels(self.query_table_columns)
        self.render_next_table_chunk(TABLE_FEATURE_INITIAL_CHUNK_SIZE)
        self.show_query_render_status()
        self.show_toast(f"Query completed: {total_features} features; scroll table to load more rows")

    def reset_query_render_state(self, features: list[dict[str, Any]] | None = None):
        self.query_features = features or []
        self.query_table_columns = []
        self.rendered_table_feature_count = 0
        self.rendered_map_feature_count = 0
        self.loading_table_chunk = False

    def initial_table_columns(self, features: list[dict[str, Any]]) -> list[str]:
        metadata_fields = []
        if isinstance(self.current_layer_metadata, dict):
            for field in self.current_layer_metadata.get("fields", []):
                if isinstance(field, dict) and field.get("name"):
                    metadata_fields.append(str(field["name"]))

        first_attrs = [
            f.get("attributes", {})
            for f in features[:TABLE_FEATURE_INITIAL_CHUNK_SIZE]
            if isinstance(f, dict) and isinstance(f.get("attributes", {}), dict)
        ]
        discovered = sorted({key for row in first_attrs for key in row.keys()})
        columns = list(dict.fromkeys([*metadata_fields, *discovered]))
        return columns or discovered

    def on_table_scroll(self, value: int):
        scrollbar = self.table.verticalScrollBar()
        if scrollbar.maximum() - value <= 20:
            self.render_next_table_chunk(TABLE_FEATURE_CHUNK_SIZE)

    def render_next_table_chunk(self, chunk_size: int = TABLE_FEATURE_CHUNK_SIZE):
        if self.loading_table_chunk or self.rendered_table_feature_count >= len(self.query_features):
            return
        self.loading_table_chunk = True
        try:
            start = self.rendered_table_feature_count
            end = min(start + chunk_size, len(self.query_features))
            chunk = self.query_features[start:end]
            attrs = [
                f.get("attributes", {})
                for f in chunk
                if isinstance(f, dict) and isinstance(f.get("attributes", {}), dict)
            ]
            self.ensure_table_columns(attrs)

            self.table.blockSignals(True)
            try:
                self.table.setRowCount(end)
                for absolute_row, feature in enumerate(chunk, start):
                    row_attrs = feature.get("attributes", {}) if isinstance(feature, dict) else {}
                    if not isinstance(row_attrs, dict):
                        row_attrs = {}
                    for col_idx, col_name in enumerate(self.query_table_columns):
                        value = row_attrs.get(col_name, "")
                        item = QTableWidgetItem("" if value is None else str(value))
                        item.setData(Qt.UserRole, absolute_row)
                        self.table.setItem(absolute_row, col_idx, item)
                if start == 0:
                    self.table.resizeColumnsToContents()
            finally:
                self.table.blockSignals(False)

            self.rendered_table_feature_count = end
            self.update_lazy_map_preview()
            self.show_query_render_status()
        finally:
            self.loading_table_chunk = False

    def ensure_table_columns(self, attrs: list[dict[str, Any]]):
        missing = sorted({key for row in attrs for key in row.keys()} - set(self.query_table_columns))
        if not missing:
            return
        self.query_table_columns.extend(missing)
        self.table.setColumnCount(len(self.query_table_columns))
        self.table.setHorizontalHeaderLabels(self.query_table_columns)

    def update_lazy_map_preview(self):
        next_map_count = min(
            self.rendered_table_feature_count,
            len(self.query_features),
        )
        if next_map_count <= self.rendered_map_feature_count:
            return
        map_source_features = self.query_features[:next_map_count]
        geojson_features = self.arcgis_features_to_geojson(map_source_features)
        self.last_geojson_features = geojson_features
        self.rendered_map_feature_count = next_map_count
        self.draw_features_on_map(geojson_features)
        self.update_map_status(len(self.query_features), len(geojson_features))

    def show_query_render_status(self):
        if not self.query_features:
            return
        data = self.last_response if isinstance(self.last_response, dict) else {}
        exceeded = data.get("exceededTransferLimit")
        suffix = " - transfer limit exceeded" if exceeded else ""
        pages = data.get("pagesFetched")
        pages_suffix = f" - pages {pages}" if pages else ""
        map_suffix = (
            f" - map loaded {self.rendered_map_feature_count}/{len(self.query_features)}"
            if self.rendered_map_feature_count < len(self.query_features)
            else ""
        )
        self.statusBar().showMessage(
            f"Query completed: {len(self.query_features)} features - table loaded {self.rendered_table_feature_count}/{len(self.query_features)}{pages_suffix}{map_suffix}{suffix}"
        )

    def build_response_text_preview(self, data: dict[str, Any], features: list[dict[str, Any]]) -> str:
        if len(features) <= JSON_FEATURE_PREVIEW_LIMIT:
            return json.dumps(data, indent=2, ensure_ascii=False)

        preview = dict(data)
        preview["features"] = features[:JSON_FEATURE_PREVIEW_LIMIT]
        preview["_arcgisRestExplorerPreview"] = {
            "message": "JSON preview truncated to keep the UI responsive. Full response is still available for export.",
            "featuresShown": JSON_FEATURE_PREVIEW_LIMIT,
            "featuresTotal": len(features),
        }
        return json.dumps(preview, indent=2, ensure_ascii=False)

    def update_map_status(self, feature_count: int, geometry_count: int):
        meta = self.current_layer_metadata or {}
        renderer = meta.get("drawingInfo", {}).get("renderer", {})
        status = {
            "layer": meta.get("name"),
            "features": feature_count,
            "geometriesMapped": geometry_count,
            "geometryType": meta.get("geometryType"),
            "wkid": self.detect_wkid(),
            "rendererType": renderer.get("type") if isinstance(renderer, dict) else None,
            "mapStyle": self.map_style_preset,
            "basemap": self.basemap_combo.currentText() if hasattr(self, "basemap_combo") else None,
            "queryBuilder": "domain-aware",
            "lastRequestMs": round(self.last_request_elapsed_ms, 1) if self.last_request_elapsed_ms is not None else None,
            "lastRequestUrlAvailable": bool(self.last_request_url),
            "collections": len(self.collections),
            "historyEntries": len(self.history),
            "spatialFilterActive": bool(self.spatial_filter_geometry),
            "spatialFilterType": self.spatial_filter_geometry_type,
        }
        self.map_status.setPlainText(json.dumps(status, indent=2, ensure_ascii=False))

    def get_map_features(self, geojson_features: list[dict[str, Any]]) -> list[dict[str, Any]]:
        features = list(geojson_features)
        if self.spatial_filter_geojson_feature:
            features = [
                feature for feature in features
                if not feature.get("properties", {}).get("__spatialFilter")
            ]
            features.append(self.spatial_filter_geojson_feature)
        return features

    def draw_features_on_map(self, geojson_features: list[dict[str, Any]]):
        if WEBENGINE_AVAILABLE and self.map_view is not None:
            html = self.build_map_html(self.get_map_features(geojson_features))
            if len(html) <= MAP_SETHTML_MAX_CHARS:
                self.map_view.setHtml(html, QUrl.fromLocalFile(str(PACKAGE_DIR / "map_preview.html")))
                return
            try:
                MAP_HTML_FILE.write_text(html, encoding="utf-8")
                map_url = QUrl.fromLocalFile(str(MAP_HTML_FILE))
                map_url.setQuery(f"v={time.time_ns()}")
                self.map_view.load(map_url)
            except Exception:
                logger.exception("Failed to load map preview from local HTML file")
                self.map_view.setHtml(html, QUrl.fromLocalFile(str(PACKAGE_DIR / "map_preview.html")))

    def on_map_feature_clicked(self, feature_index: int):
        while feature_index >= self.table.rowCount() and self.rendered_table_feature_count < len(self.query_features):
            self.render_next_table_chunk(TABLE_FEATURE_CHUNK_SIZE)
        if feature_index < 0 or feature_index >= self.table.rowCount():
            return
        self.table_selection_from_map = True
        self.table.selectRow(feature_index)
        self.table.scrollToItem(self.table.item(feature_index, 0))
        self.table_selection_from_map = False
        self.statusBar().showMessage(f"Selected feature from map: row {feature_index + 1}")

    def on_table_selection_changed(self):
        if self.table_selection_from_map:
            return
        selected = self.table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        self.select_feature_on_map(row)
        self.statusBar().showMessage(f"Selected feature from table: row {row + 1}")

    def select_feature_on_map(self, feature_index: int):
        if WEBENGINE_AVAILABLE and self.map_view is not None:
            js = f"if (window.selectFeatureFromPython) window.selectFeatureFromPython({feature_index});"
            self.map_view.page().runJavaScript(js)

    def arcgis_features_to_geojson(self, features: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        response_wkid = self.detect_wkid()
        aliases = self.get_field_aliases()
        object_id_field = self.get_object_id_field()

        for index, feature in enumerate(features):
            geometry = feature.get("geometry")
            attributes = dict(feature.get("attributes", {}))
            if not geometry:
                continue
            geojson_geometry = self.arcgis_geometry_to_geojson_geometry(geometry, self.detect_geometry_wkid(geometry, response_wkid))
            if not geojson_geometry:
                continue
            attributes["__featureIndex"] = index
            attributes["__aliases"] = aliases
            attributes["__objectIdField"] = object_id_field
            out.append({"type": "Feature", "geometry": geojson_geometry, "properties": attributes})
        return out

    def detect_wkid(self) -> int | None:
        response = self.last_response if isinstance(self.last_response, dict) else {}
        response_sr = response.get("spatialReference")
        if isinstance(response_sr, dict):
            wkid = self.parse_wkid(response_sr.get("latestWkid") or response_sr.get("wkid"))
            if wkid is not None:
                return wkid

        if self.last_query_out_wkid is not None:
            return self.last_query_out_wkid

        if not self.current_layer_metadata:
            return None
        candidates = [
            self.current_layer_metadata.get("extent", {}).get("spatialReference", {}),
            self.current_layer_metadata.get("spatialReference", {}),
        ]
        for sr in candidates:
            if isinstance(sr, dict):
                wkid = self.parse_wkid(sr.get("latestWkid") or sr.get("wkid"))
                if wkid is not None:
                    return wkid
        return None

    def detect_geometry_wkid(self, geometry: dict[str, Any], fallback_wkid: int | None) -> int | None:
        sr = geometry.get("spatialReference")
        if isinstance(sr, dict):
            wkid = self.parse_wkid(sr.get("latestWkid") or sr.get("wkid"))
            if wkid is not None:
                return wkid
        return fallback_wkid

    @staticmethod
    def parse_wkid(value: Any) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None

    def arcgis_geometry_to_geojson_geometry(self, geometry: dict[str, Any], wkid: int | None) -> dict[str, Any] | None:
        return geom_utils.arcgis_geometry_to_geojson_geometry(geometry, wkid)

    def to_lon_lat(self, x: float, y: float, wkid: int | None) -> tuple[float, float]:
        return geom_utils.to_lon_lat(x, y, wkid)

    @staticmethod
    def webmercator_to_wgs84(x: float, y: float) -> tuple[float, float]:
        return geom_utils.webmercator_to_wgs84(x, y)


    # ---------------- Collections / History ----------------

    def load_collections(self):
        self.collections = []
        if COLLECTIONS_FILE.exists():
            data, error = load_json_file(COLLECTIONS_FILE, [])
            if error:
                backup_path = backup_corrupt_json(COLLECTIONS_FILE)
                logger.warning("%s Backup: %s", error, backup_path)
                QMessageBox.warning(self, "Collections file error", f"{error}\nBackup: {backup_path}")
            elif isinstance(data, list):
                self.collections = data
            else:
                QMessageBox.warning(self, "Collections file error", "collections.json must contain a list.")
        self.refresh_collections_tree()

    def save_collections(self):
        atomic_write_json(COLLECTIONS_FILE, self.collections)

    def refresh_collections_tree(self):
        if not hasattr(self, "collections_tree"):
            return
        self.collections_tree.clear()
        for idx, item_data in enumerate(self.collections):
            title = item_data.get("name", "Unnamed call")
            target = self.collection_target_label(item_data)
            item = QTreeWidgetItem([title, target])
            item.setData(0, Qt.UserRole, idx)
            self.collections_tree.addTopLevelItem(item)
        self.collections_tree.resizeColumnToContents(0)

    def collection_target_label(self, item_data: dict[str, Any]) -> str:
        call_type = item_data.get("call_type", "query")
        if call_type == "operation":
            operation = item_data.get("operation", {})
            label = operation.get("label") if isinstance(operation, dict) else ""
            target_name = item_data.get("target_name") or item_data.get("target_url", "")
            return f"{label} - {target_name}" if label else target_name
        return item_data.get("layer_name") or item_data.get("layer_url", "")

    def save_current_call_to_collection(self):
        if self.should_save_current_operation_call():
            self.save_current_operation_to_collection()
            return
        self.save_current_query_to_collection()

    def should_save_current_operation_call(self) -> bool:
        if not hasattr(self, "request_tabs"):
            return False
        current_tab = self.request_tabs.tabText(self.request_tabs.currentIndex())
        return current_tab == "Operations" and bool(self.current_operation_url)

    def save_current_query_to_collection(self):
        if not self.current_layer_url:
            QMessageBox.warning(self, "No call selected", "Select a layer or GP operation before saving a call.")
            return

        default_name = self.current_layer_metadata.get("name", "Layer query") if self.current_layer_metadata else "Layer query"
        name, ok = QInputDialog.getText(self, "Save Call", "Call name:", text=default_name)
        if not ok or not name.strip():
            return

        query = {
            "call_type": "query",
            "name": name.strip(),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "base_url": self.base_url.text().strip(),
            "layer_url": self.current_layer_url,
            "layer_name": self.current_layer_metadata.get("name") if self.current_layer_metadata else "",
            "where": self.where_input.text().strip() or "1=1",
            "out_fields": self.out_fields.text().strip() or "*",
            "return_geometry": self.return_geometry.isChecked(),
            "fetch_all_pages": self.fetch_all_pages.isChecked(),
            "max_records": self.max_records.currentText(),
            "order_by": self.order_by.text().strip(),
        }

        self.collections.append(query)
        self.save_collections()
        self.refresh_collections_tree()
        self.statusBar().showMessage(f"Call saved: {query['name']}")

    def save_current_operation_to_collection(self):
        if not self.current_operation_url:
            QMessageBox.warning(self, "No operation selected", "Select a layer operation or GP task before saving a call.")
            return
        operation = self.operation_combo.currentData() if hasattr(self, "operation_combo") else None
        if not isinstance(operation, dict):
            QMessageBox.warning(self, "No operation selected", "Select an operation before saving a call.")
            return
        params = self.read_operation_params()
        if params is None:
            return
        params = self.normalize_operation_params(params)
        if params is None:
            return

        default_name = operation.get("label") or ("GP call" if self.current_operation_kind == "gp_task" else "REST operation")
        name, ok = QInputDialog.getText(self, "Save Call", "Call name:", text=str(default_name))
        if not ok or not name.strip():
            return

        call = {
            "call_type": "operation",
            "name": name.strip(),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "base_url": self.base_url.text().strip(),
            "target_url": self.current_operation_url,
            "target_kind": self.current_operation_kind,
            "target_name": self.current_operation_metadata.get("name") if isinstance(self.current_operation_metadata, dict) else "",
            "target_metadata": self.current_operation_metadata or {},
            "operation": {
                "label": operation.get("label"),
                "endpoint": operation.get("endpoint"),
                "mode": operation.get("mode"),
            },
            "params": params,
        }

        self.collections.append(call)
        self.save_collections()
        self.refresh_collections_tree()
        self.statusBar().showMessage(f"Call saved: {call['name']}")

    def selected_collection_index(self) -> int | None:
        items = self.collections_tree.selectedItems() if hasattr(self, "collections_tree") else []
        if not items:
            return None
        idx = items[0].data(0, Qt.UserRole)
        return idx if isinstance(idx, int) else None

    def load_selected_collection_call(self):
        idx = self.selected_collection_index()
        if idx is None or idx >= len(self.collections):
            QMessageBox.information(self, "No call selected", "Select a saved call first.")
            return

        call = self.collections[idx]
        if call.get("call_type", "query") == "operation":
            self.load_operation_call(call)
            return
        self.load_query_call(call)

    def load_selected_collection_query(self):
        self.load_selected_collection_call()

    def load_query_call(self, query: dict[str, Any]):
        self.base_url.setText(query.get("base_url", self.base_url.text()))
        self.current_layer_url = query.get("layer_url")
        self.where_input.setText(query.get("where", "1=1"))
        self.out_fields.setText(query.get("out_fields", "*"))
        self.return_geometry.setChecked(bool(query.get("return_geometry", True)))
        self.fetch_all_pages.setChecked(bool(query.get("fetch_all_pages", False)))
        max_records = str(query.get("max_records", "100"))
        ix = self.max_records.findText(max_records)
        if ix >= 0:
            self.max_records.setCurrentIndex(ix)
        self.order_by.setText(query.get("order_by", ""))

        if self.current_layer_url:
            self._get_json(self.current_layer_url, self.show_layer_metadata)
        if hasattr(self, "request_tabs"):
            self.request_tabs.setCurrentIndex(0)
        self.statusBar().showMessage(f"Call loaded: {query.get('name', '')}")

    def load_operation_call(self, call: dict[str, Any]):
        self.base_url.setText(call.get("base_url", self.base_url.text()))
        target_url = call.get("target_url")
        target_kind = call.get("target_kind")
        metadata = call.get("target_metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        self.update_operation_panel(target_kind, target_url, metadata)

        saved_operation = call.get("operation", {})
        if isinstance(saved_operation, dict):
            self.select_saved_operation(saved_operation)
        params = call.get("params", {})
        if not isinstance(params, dict):
            params = {}
        self.operation_params.setPlainText(json.dumps(params, indent=2, ensure_ascii=False))
        if hasattr(self, "request_tabs"):
            ix = self.request_tabs.indexOf(self.operation_params.parentWidget())
            self.request_tabs.setCurrentIndex(1 if ix < 0 else ix)
        self.statusBar().showMessage(f"Call loaded: {call.get('name', '')}")

    def select_saved_operation(self, saved_operation: dict[str, Any]):
        for index in range(self.operation_combo.count()):
            operation = self.operation_combo.itemData(index)
            if not isinstance(operation, dict):
                continue
            if (
                operation.get("endpoint") == saved_operation.get("endpoint")
                and operation.get("mode") == saved_operation.get("mode")
            ):
                self.operation_combo.setCurrentIndex(index)
                return
        label = saved_operation.get("label")
        if label:
            index = self.operation_combo.findText(str(label))
            if index >= 0:
                self.operation_combo.setCurrentIndex(index)

    def delete_selected_collection_query(self):
        idx = self.selected_collection_index()
        if idx is None or idx >= len(self.collections):
            QMessageBox.information(self, "No call selected", "Select a saved call first.")
            return
        removed = self.collections.pop(idx)
        self.save_collections()
        self.refresh_collections_tree()
        self.statusBar().showMessage(f"Call deleted: {removed.get('name', '')}")

    def load_history(self):
        self.history = []
        if HISTORY_FILE.exists():
            data, error = load_json_file(HISTORY_FILE, [])
            if error:
                backup_path = backup_corrupt_json(HISTORY_FILE)
                logger.warning("%s Backup: %s", error, backup_path)
                QMessageBox.warning(self, "History file error", f"{error}\nBackup: {backup_path}")
            elif isinstance(data, list):
                self.history = data
            else:
                QMessageBox.warning(self, "History file error", "history.json must contain a list.")
        self.refresh_history_tree()

    def save_history(self):
        atomic_write_json(HISTORY_FILE, self.history[:100])

    def add_history_entry(self, elapsed_ms: float):
        if not self.last_request_url:
            return
        url = self.redact_token_from_url(self.last_request_url)
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "url": url,
            "elapsed_ms": round(elapsed_ms, 1),
            "layer": self.current_layer_metadata.get("name") if self.current_layer_metadata else "",
        }
        self.history.insert(0, entry)
        self.history = self.history[:100]
        self.save_history()
        self.refresh_history_tree()

    def refresh_history_tree(self):
        if not hasattr(self, "history_tree"):
            return
        self.history_tree.clear()
        for idx, entry in enumerate(self.history):
            label = f"{entry.get('time', '')}  {entry.get('layer') or entry.get('url', '')[:48]}"
            item = QTreeWidgetItem([label, str(entry.get("elapsed_ms", ""))])
            item.setData(0, Qt.UserRole, idx)
            self.history_tree.addTopLevelItem(item)
        self.history_tree.resizeColumnToContents(0)

    def copy_selected_history_url(self):
        items = self.history_tree.selectedItems() if hasattr(self, "history_tree") else []
        if not items:
            return
        idx = items[0].data(0, Qt.UserRole)
        if isinstance(idx, int) and idx < len(self.history):
            QApplication.clipboard().setText(self.history[idx].get("url", ""))
            self.statusBar().showMessage("History URL copied to clipboard")

    def clear_history(self):
        self.history = []
        self.save_history()
        self.refresh_history_tree()
        self.statusBar().showMessage("Request history cleared")

    def load_geometry_history(self):
        self.geometry_history = []
        if GEOMETRY_HISTORY_FILE.exists():
            data, error = load_json_file(GEOMETRY_HISTORY_FILE, [])
            if error:
                backup_path = backup_corrupt_json(GEOMETRY_HISTORY_FILE)
                logger.warning("%s Backup: %s", error, backup_path)
            elif isinstance(data, list):
                self.geometry_history = data[:10]
        self.refresh_geometry_history_combo()

    def save_geometry_history(self):
        atomic_write_json(GEOMETRY_HISTORY_FILE, self.geometry_history[:10])

    def refresh_geometry_history_combo(self):
        if not hasattr(self, "geometry_lab_history_combo"):
            return
        self.geometry_lab_history_combo.blockSignals(True)
        self.geometry_lab_history_combo.clear()
        self.geometry_lab_history_combo.addItem("-- geometry history --", None)
        for idx, entry in enumerate(self.geometry_history):
            self.geometry_lab_history_combo.addItem(entry.get("label", f"Geometry {idx + 1}"), idx)
        self.geometry_lab_history_combo.blockSignals(False)

    def add_geometry_history_entry(self, geojson_geometry: dict[str, Any]):
        text = self.geometry_lab_input.toPlainText().strip()
        if not text:
            return
        summary = self.build_geometry_summary(geojson_geometry)
        label = f"{summary['type']} {datetime.now().strftime('%H:%M:%S')} bbox={summary['bbox']}"
        entry = {
            "label": label,
            "format": self.geometry_lab_format_combo.currentText(),
            "text": text,
            "inputSR": self.geometry_lab_input_sr_combo.currentText(),
            "spatialRel": self.geometry_lab_spatial_rel_combo.currentText(),
        }
        self.geometry_history = [
            item for item in self.geometry_history
            if item.get("text") != text or item.get("spatialRel") != entry["spatialRel"]
        ]
        self.geometry_history.insert(0, entry)
        self.geometry_history = self.geometry_history[:10]
        self.save_geometry_history()
        self.refresh_geometry_history_combo()

    def load_selected_geometry_history(self, index: int):
        idx = self.geometry_lab_history_combo.itemData(index)
        if not isinstance(idx, int) or idx >= len(self.geometry_history):
            return
        self.set_geometry_lab_expanded(True)
        entry = self.geometry_history[idx]
        self.geometry_lab_input.setPlainText(entry.get("text", ""))
        fmt = entry.get("format", "Auto")
        fmt_ix = self.geometry_lab_format_combo.findText(fmt)
        if fmt_ix >= 0:
            self.geometry_lab_format_combo.setCurrentIndex(fmt_ix)
        rel_ix = self.geometry_lab_spatial_rel_combo.findText(entry.get("spatialRel", ""))
        if rel_ix >= 0:
            self.geometry_lab_spatial_rel_combo.setCurrentIndex(rel_ix)
        sr_ix = self.geometry_lab_input_sr_combo.findText(entry.get("inputSR", "Auto"))
        if sr_ix >= 0:
            self.geometry_lab_input_sr_combo.setCurrentIndex(sr_ix)
        self.statusBar().showMessage("Geometry Lab history loaded")

    # ---------------- Auth / Diagnostics ----------------

    def open_generate_token_dialog(self):
        self.current_auth_config = self.normalize_auth_config(self.current_auth_config, self.normalize_services_url())
        if self.current_auth_config.get("mode") == "manual":
            QMessageBox.information(
                self,
                "Manual auth mode",
                "This connection is configured for manual/no token generation. Open Auth Settings to configure a token endpoint.",
            )
            return
        dialog_config = dict(self.current_auth_config)
        connection_name = self.connection_name.text().strip()
        if connection_name:
            username, password = get_saved_credentials(connection_name)
            if username and password:
                dialog_config.update(
                    {
                        "username": username,
                        "password": password,
                        "remember_credentials": True,
                    }
                )
        dialog = GenerateTokenDialog(self.normalize_services_url(), dialog_config, self, verify_ssl=self.verify_ssl)
        if dialog.exec() == QDialog.Accepted and dialog.generated_token:
            generated_auth_config = dialog.get_auth_config()
            credentials_storage = self.persist_generated_credentials_for_current_connection(generated_auth_config)
            self.current_auth_config = self.normalize_auth_config(
                self.auth_config_for_storage(generated_auth_config),
                self.normalize_services_url(),
            )
            self.token_input.setText(dialog.generated_token)
            if dialog.expires_text:
                self.token_expiry_label.setText(f"Expires: {dialog.expires_text}")
            else:
                self.token_expiry_label.setText("Token generated")
            token_storage = self.persist_generated_token_for_current_connection(dialog.generated_token)
            self.update_auth_status(token_storage)
            suffix = f"; credentials {credentials_storage}" if credentials_storage else ""
            self.statusBar().showMessage(f"Token applied to current connection{suffix}")

    def persist_generated_credentials_for_current_connection(self, auth_config: dict[str, Any]) -> str:
        name = self.connection_name.text().strip()
        if not name:
            return ""
        if not auth_config.get("remember_credentials"):
            delete_credentials(name)
            return "not saved"
        saved = save_credentials(name, auth_config.get("username", ""), auth_config.get("password", ""))
        return "saved in keyring" if saved else "not saved"

    def persist_generated_token_for_current_connection(self, token: str) -> str:
        name = self.connection_name.text().strip()
        if not name:
            return "session_only"

        token_saved = save_token(name, token)
        token_storage = "keyring" if token_saved else "session_only"
        existing = next((c for c in self.connections if c.get("name") == name), None)
        if existing is not None:
            existing.update(
                {
                    "url": self.base_url.text().strip(),
                    "token_expires": self.token_expiry_label.text(),
                    "token_storage": token_storage,
                    "auth": self.auth_config_for_storage(),
                }
            )
            existing.pop("token", None)
            atomic_write_json(CONNECTIONS_FILE, self.connections)
            self.refresh_connection_combo()
        return token_storage

    def clear_current_token(self):
        self.token_input.clear()
        self.token_expiry_label.setText("Token: not checked")
        self.update_auth_status("none")
        self.statusBar().showMessage("Current token cleared from session")

    def forget_saved_token(self):
        name = self.connection_name.text().strip()
        if not name:
            QMessageBox.information(self, "No connection", "Select or enter a connection name first.")
            return
        delete_token(name)
        for conn in self.connections:
            if conn.get("name") == name:
                conn["token_storage"] = "none"
                conn.pop("token", None)
        atomic_write_json(CONNECTIONS_FILE, self.connections)
        self.token_input.clear()
        self.token_expiry_label.setText("Token: not checked")
        self.update_auth_status("none")
        self.statusBar().showMessage(f"Saved token forgotten: {name}")

    def update_auth_status(self, token_storage: str | None = None):
        mode = (self.current_auth_config or {}).get("mode", "server")
        mode_label = {
            "manual": "manual",
            "server": "ArcGIS Server standalone",
            "portal": "Federated via Portal",
        }.get(mode, mode)
        token = self.get_token()
        if not token:
            self.auth_status_label.setText(f"Auth: no token ({mode_label})")
            return
        if token_storage == "keyring":
            self.auth_status_label.setText(f"Auth: saved in keyring ({mode_label})")
        elif token_storage == "session_only" or not KEYRING_AVAILABLE:
            self.auth_status_label.setText(f"Auth: session token ({mode_label})")
        else:
            self.auth_status_label.setText(f"Auth: token loaded ({mode_label})")

    def copy_last_request_url(self):
        if not self.last_request_url:
            QMessageBox.information(self, "No request", "No request URL available yet.")
            return
        QApplication.clipboard().setText(self.redact_token_from_url(self.last_request_url))
        self.statusBar().showMessage("Last request URL copied to clipboard")

    @staticmethod
    def redact_token_from_url(url: str) -> str:
        sensitive_keys = {"token", "access_token", "apikey", "api_key", "key", "bearer"}
        try:
            parsed = httpx.URL(url)
            pairs = []
            for key, value in parsed.params.multi_items():
                pairs.append((key, "<REDACTED>" if key.lower() in sensitive_keys else value))
            return str(parsed.copy_with(params=pairs))
        except Exception:
            redacted = url
            for key in sensitive_keys:
                redacted = redacted.replace(f"{key}=", f"{key}=<REDACTED>")
                redacted = redacted.replace(f"{key.upper()}=", f"{key.upper()}=<REDACTED>")
            return redacted


    # ---------------- Embedded Geometry Lab ----------------

    def load_geometry_lab_sample_point(self):
        self.set_geometry_lab_expanded(True)
        self.geometry_lab_format_combo.setCurrentText("WKT")
        self.geometry_lab_input.setPlainText("POINT (12.4924 41.8902)")
        self.statusBar().showMessage("Geometry Lab sample point loaded")

    def load_geometry_lab_sample_polygon(self):
        self.set_geometry_lab_expanded(True)
        self.geometry_lab_format_combo.setCurrentText("WKT")
        self.geometry_lab_input.setPlainText(
            "POLYGON ((12.485 41.887, 12.501 41.887, 12.501 41.895, 12.485 41.895, 12.485 41.887))"
        )
        self.statusBar().showMessage("Geometry Lab sample polygon loaded")

    def import_geometry_lab(self):
        self.set_geometry_lab_expanded(True)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import geometry",
            "",
            "Geometry files (*.geojson *.json *.wkt *.txt);;All files (*.*)",
        )
        if not path:
            return
        text = Path(path).read_text(encoding="utf-8")
        suffix = Path(path).suffix.lower()
        if suffix == ".wkt" or suffix == ".txt":
            self.geometry_lab_format_combo.setCurrentText("WKT")
        elif suffix in (".geojson", ".json"):
            self.geometry_lab_format_combo.setCurrentText("Auto")
        self.geometry_lab_input.setPlainText(text)
        self.statusBar().showMessage(f"Geometry imported: {path}")

    def export_geometry_lab(self):
        try:
            geojson_features, arcgis_geometry, arcgis_geometry_type = self.parse_geometry_lab_widgets()
        except Exception as exc:
            QMessageBox.critical(self, "Geometry export error", str(exc))
            return

        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export geometry",
            "geometry.geojson",
            "GeoJSON (*.geojson);;ArcGIS JSON (*.json);;WKT (*.wkt)",
        )
        if not path:
            return

        geojson_geometry = geojson_features[0]["geometry"]
        if selected_filter.startswith("ArcGIS"):
            payload = {"geometry": arcgis_geometry, "geometryType": arcgis_geometry_type}
            Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        elif selected_filter.startswith("WKT"):
            Path(path).write_text(self.geojson_geometry_to_wkt(geojson_geometry), encoding="utf-8")
        else:
            payload = {"type": "FeatureCollection", "features": geojson_features}
            Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        self.statusBar().showMessage(f"Geometry exported: {path}")
        self.notify_export_done("Geometry", path)

    def copy_geometry_lab_output(self, mode: str):
        try:
            geojson_features, arcgis_geometry, arcgis_geometry_type = self.parse_geometry_lab_widgets()
            geojson_geometry = geojson_features[0]["geometry"]
            if mode == "geojson":
                text = json.dumps(geojson_geometry, indent=2, ensure_ascii=False)
            elif mode == "arcgis":
                text = json.dumps(
                    {"geometry": arcgis_geometry, "geometryType": arcgis_geometry_type},
                    indent=2,
                    ensure_ascii=False,
                )
            else:
                text = self.geometry_lab_output.toPlainText()
            QApplication.clipboard().setText(text)
            self.statusBar().showMessage(f"Geometry Lab {mode} copied to clipboard")
        except Exception as exc:
            QMessageBox.critical(self, "Geometry copy error", str(exc))

    def parse_geometry_lab_widgets(self) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
        text = self.geometry_lab_input.toPlainText().strip()
        if not text:
            raise ValueError("Paste a geometry first.")

        parser = GeometryLabDialog(self)
        geojson_geometry = parser.parse_geometry(text, self.geometry_lab_format_combo.currentText())
        input_wkid = self.detect_geometry_lab_input_wkid(text)
        geojson_geometry = self.transform_geojson_geometry_to_wgs84(geojson_geometry, input_wkid)
        geojson_geometry = self.normalize_geojson_geometry(geojson_geometry)
        self.validate_geojson_geometry(geojson_geometry)
        arcgis_geometry, arcgis_geometry_type = parser.geojson_geometry_to_arcgis(geojson_geometry)

        geojson_features = [{
            "type": "Feature",
            "geometry": geojson_geometry,
            "properties": {
                "__featureIndex": 0,
                "source": "Geometry Lab",
                "geometryType": arcgis_geometry_type,
                "__objectIdField": "source",
                "__aliases": {"source": "Source", "geometryType": "Geometry Type"},
            },
        }]

        params = {
            "geometry": arcgis_geometry,
            "geometryType": arcgis_geometry_type,
            "inSR": 4326,
            "spatialRel": self.geometry_lab_spatial_rel_combo.currentText(),
        }
        self.geometry_lab_output.setPlainText(json.dumps(params, indent=2, ensure_ascii=False))
        self.geometry_lab_summary.setPlainText(json.dumps(self.build_geometry_summary(geojson_geometry), indent=2, ensure_ascii=False))
        return geojson_features, arcgis_geometry, arcgis_geometry_type

    def normalize_geojson_geometry(self, geometry: dict[str, Any]) -> dict[str, Any]:
        geometry = dict(geometry)
        gtype = geometry.get("type")
        coords = geometry.get("coordinates")
        if gtype == "Polygon" and isinstance(coords, list):
            geometry["coordinates"] = [self.close_ring_if_needed(ring) for ring in coords]
        elif gtype == "MultiPolygon" and isinstance(coords, list):
            geometry["coordinates"] = [
                [self.close_ring_if_needed(ring) for ring in polygon]
                for polygon in coords
            ]
        return geometry

    @staticmethod
    def close_ring_if_needed(ring: list[Any]) -> list[Any]:
        if not ring:
            return ring
        out = list(ring)
        if out[0] != out[-1]:
            out.append(out[0])
        return out

    def validate_geojson_geometry(self, geometry: dict[str, Any]) -> None:
        coords = list(self.iter_geojson_positions(geometry))
        if not coords:
            raise ValueError("Geometry has no coordinates.")
        for lon, lat in coords:
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError(f"Coordinate outside WGS84 lon/lat range: {lon}, {lat}")

        gtype = geometry.get("type")
        if gtype == "Polygon":
            for ring in geometry.get("coordinates", []):
                if len(ring) < 4:
                    raise ValueError("Polygon rings need at least 4 coordinates.")
                if ring[0] != ring[-1]:
                    raise ValueError("Polygon ring is not closed.")
        elif gtype == "MultiPolygon":
            for polygon in geometry.get("coordinates", []):
                for ring in polygon:
                    if len(ring) < 4:
                        raise ValueError("MultiPolygon rings need at least 4 coordinates.")
                    if ring[0] != ring[-1]:
                        raise ValueError("MultiPolygon ring is not closed.")

    def build_geometry_summary(self, geometry: dict[str, Any]) -> dict[str, Any]:
        coords = list(self.iter_geojson_positions(geometry))
        xs = [p[0] for p in coords]
        ys = [p[1] for p in coords]
        bbox = [min(xs), min(ys), max(xs), max(ys)]
        centroid = [sum(xs) / len(xs), sum(ys) / len(ys)]
        return {
            "type": geometry.get("type"),
            "vertices": len(coords),
            "bbox": [round(v, 6) for v in bbox],
            "centroid": [round(v, 6) for v in centroid],
            "spatialReference": "EPSG:4326",
            "inputSpatialReference": self.geometry_lab_input_sr_combo.currentText(),
            "spatialRel": self.geometry_lab_spatial_rel_combo.currentText(),
        }

    def detect_geometry_lab_input_wkid(self, text: str) -> int:
        selected = self.geometry_lab_input_sr_combo.currentText()
        if "3857" in selected:
            return 3857
        if "4326" in selected:
            return 4326
        try:
            data = json.loads(text)
        except Exception:
            return 4326
        if isinstance(data, dict):
            for candidate in (data, data.get("geometry", {})):
                if isinstance(candidate, dict):
                    sr = candidate.get("spatialReference")
                    if isinstance(sr, dict):
                        wkid = sr.get("latestWkid") or sr.get("wkid")
                        if isinstance(wkid, int):
                            return wkid
        return 4326

    def transform_geojson_geometry_to_wgs84(self, geometry: dict[str, Any], wkid: int) -> dict[str, Any]:
        if wkid not in (3857, 102100, 102113):
            return geometry

        def transform(value):
            if (
                isinstance(value, list)
                and len(value) >= 2
                and isinstance(value[0], (int, float))
                and isinstance(value[1], (int, float))
            ):
                lon, lat = geom_utils.webmercator_to_wgs84(value[0], value[1])
                return [lon, lat, *value[2:]]
            if isinstance(value, list):
                return [transform(item) for item in value]
            return value

        transformed = dict(geometry)
        transformed["coordinates"] = transform(geometry.get("coordinates"))
        return transformed

    def iter_geojson_positions(self, geometry: dict[str, Any]):
        def walk(value):
            if (
                isinstance(value, list)
                and len(value) >= 2
                and isinstance(value[0], (int, float))
                and isinstance(value[1], (int, float))
            ):
                yield float(value[0]), float(value[1])
                return
            if isinstance(value, list):
                for item in value:
                    yield from walk(item)

        yield from walk(geometry.get("coordinates"))

    def geojson_geometry_to_wkt(self, geometry: dict[str, Any]) -> str:
        def pair(point):
            return f"{point[0]} {point[1]}"

        gtype = geometry.get("type")
        coords = geometry.get("coordinates")
        if gtype == "Point":
            return f"POINT ({pair(coords)})"
        if gtype == "LineString":
            return "LINESTRING (" + ", ".join(pair(p) for p in coords) + ")"
        if gtype == "Polygon":
            rings = ["(" + ", ".join(pair(p) for p in ring) + ")" for ring in coords]
            return "POLYGON (" + ", ".join(rings) + ")"
        raise ValueError(f"WKT export currently supports Point, LineString and Polygon. Got: {gtype}")

    def preview_geometry_lab(self):
        try:
            geojson_features, _, arcgis_geometry_type = self.parse_geometry_lab_widgets()
            self.last_geojson_features = geojson_features
            self.draw_features_on_map(geojson_features)
            self.update_map_status(0, len(geojson_features))
            self.add_geometry_history_entry(geojson_features[0]["geometry"])
            self.statusBar().showMessage(f"Geometry Lab preview: {arcgis_geometry_type}")
        except Exception as exc:
            QMessageBox.critical(self, "Geometry Lab parse error", str(exc))

    def use_geometry_lab_as_spatial_filter(self):
        try:
            geojson_features, arcgis_geometry, arcgis_geometry_type = self.parse_geometry_lab_widgets()
            self.spatial_filter_geometry = arcgis_geometry
            self.spatial_filter_geometry_type = arcgis_geometry_type
            self.spatial_filter_geojson_feature = dict(geojson_features[0])
            self.spatial_filter_geojson_feature["properties"] = dict(self.spatial_filter_geojson_feature.get("properties", {}))
            self.spatial_filter_geojson_feature["properties"]["__spatialFilter"] = True
            self.spatial_filter_geojson_feature["properties"]["__featureIndex"] = -1
            self.use_spatial_filter.setChecked(True)
            self.spatial_filter_label.setText(f"Spatial filter: {arcgis_geometry_type} / WGS84")
            self.draw_features_on_map(self.last_geojson_features)
            self.update_map_status(len(self.last_geojson_features), len(self.last_geojson_features))
            self.add_geometry_history_entry(geojson_features[0]["geometry"])
            self.statusBar().showMessage("Geometry Lab spatial filter applied")
        except Exception as exc:
            QMessageBox.critical(self, "Geometry Lab parse error", str(exc))

    def clear_geometry_lab(self):
        self.geometry_lab_input.clear()
        self.geometry_lab_output.clear()
        self.geometry_lab_summary.clear()
        self.clear_spatial_filter()
        self.init_map()
        self.statusBar().showMessage("Geometry Lab cleared")

    # ---------------- Geometry Lab ----------------

    def open_geometry_lab(self):
        dialog = GeometryLabDialog(self)
        if dialog.exec() == QDialog.Accepted:
            if not dialog.arcgis_geometry or not dialog.arcgis_geometry_type:
                dialog.parse_input()
            if dialog.arcgis_geometry and dialog.arcgis_geometry_type:
                self.spatial_filter_geometry = dialog.arcgis_geometry
                self.spatial_filter_geometry_type = dialog.arcgis_geometry_type
                self.spatial_filter_geojson_feature = dict(dialog.parsed_geojson_features[0])
                self.spatial_filter_geojson_feature["properties"] = dict(self.spatial_filter_geojson_feature.get("properties", {}))
                self.spatial_filter_geojson_feature["properties"]["__spatialFilter"] = True
                self.spatial_filter_geojson_feature["properties"]["__featureIndex"] = -1
                self.use_spatial_filter.setChecked(True)
                self.spatial_filter_label.setText(f"Spatial filter: {dialog.arcgis_geometry_type} / WGS84")
                self.draw_features_on_map(self.last_geojson_features)
                self.update_map_status(len(self.last_geojson_features), len(self.last_geojson_features))
                self.statusBar().showMessage("Geometry Lab spatial filter applied")

    def clear_spatial_filter(self):
        self.spatial_filter_geometry = None
        self.spatial_filter_geometry_type = None
        self.spatial_filter_geojson_feature = None
        self.use_spatial_filter.setChecked(False)
        self.spatial_filter_label.setText("Spatial filter: none")
        self.draw_features_on_map(self.last_geojson_features)
        self.statusBar().showMessage("Spatial filter cleared")

    # ---------------- Export ----------------

    def open_json_response_context_menu(self, pos):
        menu = QMenu(self)
        save_json_action = menu.addAction("Save response as JSON...")
        save_json_action.setEnabled(self.last_response is not None)
        action = menu.exec(self.response_text.viewport().mapToGlobal(pos))
        if action == save_json_action:
            self.export_json()

    def open_table_context_menu(self, pos):
        menu = QMenu(self)
        export_csv_action = menu.addAction("Export table as CSV...")
        export_xlsx_action = menu.addAction("Export table as XLSX...")
        has_table_data = self.table.rowCount() > 0 and self.table.columnCount() > 0
        export_csv_action.setEnabled(has_table_data)
        export_xlsx_action.setEnabled(has_table_data)
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == export_csv_action:
            self.export_csv()
        elif action == export_xlsx_action:
            self.export_xlsx()

    def export_json(self):
        if self.last_response is None:
            QMessageBox.information(self, "Nothing to export", "No JSON response available.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export JSON", "response.json", "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(self.last_response, indent=2, ensure_ascii=False), encoding="utf-8")
            self.statusBar().showMessage(f"JSON exported: {path}")
            self.notify_export_done("JSON", path)

    def export_csv(self):
        if self.table.rowCount() == 0 or self.table.columnCount() == 0:
            QMessageBox.information(self, "Nothing to export", "No table data available.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "features.csv", "CSV (*.csv)")
        if not path:
            return

        headers, rows = self.get_table_export_data()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(headers)
            writer.writerows(rows)
        self.statusBar().showMessage(f"CSV exported: {path}")
        self.notify_export_done("CSV", path)

    def export_xlsx(self):
        if self.table.rowCount() == 0 or self.table.columnCount() == 0:
            QMessageBox.information(self, "Nothing to export", "No table data available.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export XLSX", "features.xlsx", "Excel Workbook (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"

        headers, rows = self.get_table_export_data()
        self.write_xlsx(Path(path), headers, rows)
        self.statusBar().showMessage(f"XLSX exported: {path}")
        self.notify_export_done("XLSX", path)

    def get_table_export_data(self) -> tuple[list[str], list[list[str]]]:
        headers = [
            self.table.horizontalHeaderItem(c).text() if self.table.horizontalHeaderItem(c) else f"Column {c + 1}"
            for c in range(self.table.columnCount())
        ]
        rows = [
            [
                self.table.item(r, c).text() if self.table.item(r, c) else ""
                for c in range(self.table.columnCount())
            ]
            for r in range(self.table.rowCount())
        ]
        return headers, rows

    def write_xlsx(self, path: Path, headers: list[str], rows: list[list[str]]):
        sheet_rows = [headers, *rows]
        sheet_xml = self.build_xlsx_sheet_xml(sheet_rows)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "[Content_Types].xml",
                """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
            )
            zf.writestr(
                "_rels/.rels",
                """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
            )
            zf.writestr(
                "xl/workbook.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Features" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>""",
            )
            zf.writestr(
                "xl/_rels/workbook.xml.rels",
                """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
            )
            zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)

    def build_xlsx_sheet_xml(self, rows: list[list[str]]) -> str:
        row_xml = []
        for row_idx, row in enumerate(rows, start=1):
            cells = []
            for col_idx, value in enumerate(row, start=1):
                ref = f"{self.xlsx_column_name(col_idx)}{row_idx}"
                safe_value = escape(str(value), {'"': "&quot;"})
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{safe_value}</t></is></c>')
            row_xml.append(f'<row r="{row_idx}">' + "".join(cells) + "</row>")
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData>"
            + "".join(row_xml)
            + "</sheetData></worksheet>"
        )

    @staticmethod
    def xlsx_column_name(index: int) -> str:
        name = ""
        while index:
            index, rem = divmod(index - 1, 26)
            name = chr(65 + rem) + name
        return name


def main():
    app = QApplication(sys.argv)
    window = ArcGISRestExplorer()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
