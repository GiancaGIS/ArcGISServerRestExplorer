import json
from datetime import datetime
from typing import Any

import httpx
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import arcgis_geometry as geom_utils


def set_form_row_visible(form: QFormLayout, widget, visible: bool) -> None:
    label = form.labelForField(widget)
    if label is not None:
        label.setVisible(visible)
    widget.setVisible(visible)


class GenerateTokenDialog(QDialog):
    def __init__(
        self,
        default_services_url: str,
        auth_config: dict[str, Any] | None = None,
        parent=None,
        verify_ssl: bool = True,
    ):
        super().__init__(parent)
        self.setWindowTitle("Generate ArcGIS Token")
        self.resize(800, 600)
        self.services_url = default_services_url
        self.generated_token = ""
        self.expires_text = ""
        self.verify_ssl = verify_ssl
        self.auth_config = auth_config or {}
        self.mode = self.auth_config.get("mode", "server")
        if self.mode not in ("server", "portal"):
            self.mode = "server"

        layout = QVBoxLayout(self)
        self.form = QFormLayout()

        token_url = self.auth_config.get("token_url") or self.default_token_url(default_services_url, self.auth_config)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("ArcGIS Server standalone", "server")
        self.mode_combo.addItem("Federated ArcGIS Server via Portal", "portal")
        ix = self.mode_combo.findData(self.mode)
        self.mode_combo.setCurrentIndex(ix if ix >= 0 else 0)
        self.mode_combo.currentIndexChanged.connect(self.on_mode_changed)

        self.token_url = QLineEdit(token_url)
        self.username = QLineEdit(self.auth_config.get("username", ""))
        self.password = QLineEdit(self.auth_config.get("password", ""))
        self.password.setEchoMode(QLineEdit.Password)
        self.remember_credentials = QCheckBox("Remember username/password in keyring for this saved connection")
        self.remember_credentials.setChecked(bool(self.auth_config.get("remember_credentials", False)))
        self.client_combo = QComboBox()
        self.referer = QLineEdit(self.auth_config.get("referer", "arcgis-rest-explorer"))
        self.ip_address = QLineEdit(self.auth_config.get("ip", ""))
        self.server_url = QLineEdit(
            self.auth_config.get("server_url") or self.default_server_url(default_services_url)
        )
        self.expiration = QSpinBox()
        self.expiration.setRange(1, 20160)
        self.expiration.setValue(int(self.auth_config.get("expiration", 60)))
        self.response_format = QComboBox()
        self.response_format.addItem("JSON", "json")
        self.response_format.addItem("Pretty JSON", "pjson")
        ix = self.response_format.findData(self.auth_config.get("response_format", "json"))
        self.response_format.setCurrentIndex(ix if ix >= 0 else 0)
        self.result = QTextEdit()
        self.result.setReadOnly(True)
        self.result.setMaximumHeight(120)
        self.help_text = QTextEdit()
        self.help_text.setReadOnly(True)
        self.help_text.setMaximumHeight(125)

        self.token_endpoint_label = QLabel("Token endpoint")
        self.configure_client_options(self.mode, self.auth_config.get("client", "referer"))
        self.client_combo.currentIndexChanged.connect(self.on_client_changed)
        self.form.addRow("Scenario", self.mode_combo)
        self.form.addRow(self.token_endpoint_label, self.token_url)
        self.form.addRow("Username", self.username)
        self.form.addRow("Password", self.password)
        self.form.addRow("", self.remember_credentials)
        self.form.addRow("Federated server URL", self.server_url)
        layout.addLayout(self.form)

        self.advanced_toggle = QPushButton("Advanced standalone parameters")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.toggled.connect(self.on_advanced_toggled)
        self.advanced_widget = QWidget()
        self.advanced_form = QFormLayout(self.advanced_widget)
        self.advanced_form.setContentsMargins(18, 0, 0, 0)
        self.advanced_form.addRow("Client", self.client_combo)
        self.advanced_form.addRow("Referer", self.referer)
        self.advanced_form.addRow("IP address", self.ip_address)
        self.advanced_form.addRow("Expiration minutes", self.expiration)
        self.advanced_form.addRow("Response format", self.response_format)
        layout.addWidget(self.advanced_toggle)
        layout.addWidget(self.advanced_widget)
        layout.addWidget(QLabel("REST call"))
        layout.addWidget(self.help_text)

        self.generate_btn = QPushButton("Generate Token")
        self.generate_btn.clicked.connect(self.generate_token)
        layout.addWidget(self.generate_btn)
        layout.addWidget(QLabel("Result"))
        layout.addWidget(self.result)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.on_mode_changed()
        self.on_client_changed()

    def on_advanced_toggled(self, checked: bool):
        self.advanced_widget.setVisible(checked)
        self.on_client_changed()

    def on_mode_changed(self):
        self.mode = self.mode_combo.currentData() or "server"
        current_client = self.client_combo.currentData() or self.auth_config.get("client", "referer")
        self.configure_client_options(self.mode, str(current_client))

        if self.mode == "portal":
            if "/sharing/rest/generateToken" not in self.token_url.text():
                self.token_url.clear()
            self.token_url.setPlaceholderText("https://your-portal.example.com/portal/sharing/rest/generateToken")
        else:
            if not self.token_url.text().strip() or "/sharing/rest/generateToken" in self.token_url.text():
                self.token_url.setText(self.default_token_url(self.services_url, {"mode": "server"}))

        self.update_mode_fields()
        self.on_client_changed()

    def generate_token(self):
        if not self.username.text().strip() or not self.password.text():
            QMessageBox.warning(self, "Missing credentials", "Insert username and password.")
            return

        payload = self.build_credentials_payload()
        if payload is None:
            return

        token_endpoint = self.token_url.text().strip()

        try:
            with httpx.Client(timeout=30.0, follow_redirects=True, verify=self.verify_ssl) as client:
                response = client.post(token_endpoint, data=payload)
                response.raise_for_status()
                data = response.json()
                self.raise_for_token_error(data)

                exchanged_for_server = False
                if self.mode == "portal" and self.server_url.text().strip():
                    portal_token = data.get("token")
                    if not portal_token:
                        raise RuntimeError("No portal token returned by server.")
                    server_payload = {
                        "token": portal_token,
                        "serverUrl": self.server_url.text().strip().rstrip("/"),
                        "f": "json",
                    }
                    response = client.post(token_endpoint, data=server_payload)
                    response.raise_for_status()
                    data = response.json()
                    self.raise_for_token_error(data)
                    exchanged_for_server = True

            token = data.get("token")
            if not token:
                raise RuntimeError("No token returned by server.")

            self.generated_token = token
            expires = data.get("expires")
            self.expires_text = ""
            if expires:
                try:
                    self.expires_text = datetime.fromtimestamp(int(expires) / 1000).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    self.expires_text = str(expires)

            preview = token[:18] + "..." + token[-8:] if len(token) > 30 else token
            if exchanged_for_server:
                token_type = "Federated ArcGIS Server token"
            elif self.mode == "portal":
                token_type = "Portal token"
            else:
                token_type = "ArcGIS Server standalone token"
            self.result.setPlainText(
                f"{token_type} generated.\n"
                f"Scenario: {self.mode_label(self.mode)}\n"
                f"Expires: {self.expires_text or 'unknown'}\n"
                f"Preview: {preview}"
            )

        except Exception as exc:
            QMessageBox.critical(self, "Token error", str(exc))

    def build_credentials_payload(self) -> dict[str, str] | None:
        payload = {
            "username": self.username.text().strip(),
            "password": self.password.text(),
            "expiration": str(self.expiration.value()),
            "f": self.response_format.currentData() or "json",
        }
        if self.mode == "server" and not self.advanced_toggle.isChecked():
            return payload

        client_type = self.client_combo.currentData() or "referer"
        payload["client"] = client_type
        if client_type == "referer":
            payload["referer"] = self.referer.text().strip() or "arcgis-rest-explorer"
        elif client_type == "ip":
            ip = self.ip_address.text().strip()
            if not ip:
                QMessageBox.warning(self, "Missing IP address", "Insert an IP address or choose a different client type.")
                return None
            payload["ip"] = ip
        return payload

    @staticmethod
    def raise_for_token_error(data: object) -> None:
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            raise RuntimeError(f"{err.get('code', '')}: {err.get('message', 'Token generation failed')}")

    def configure_client_options(self, mode: str, selected: str):
        self.client_combo.blockSignals(True)
        self.client_combo.clear()
        self.client_combo.addItem("Referer", "referer")
        self.client_combo.addItem("IP address", "ip")
        if mode == "server":
            self.client_combo.addItem("Request IP", "requestip")
        ix = self.client_combo.findData(selected)
        self.client_combo.setCurrentIndex(ix if ix >= 0 else 0)
        self.client_combo.blockSignals(False)

    def update_mode_fields(self):
        self.token_endpoint_label.setText("Portal token URL" if self.mode == "portal" else "ArcGIS Server token URL")
        is_portal = self.mode == "portal"
        set_form_row_visible(self.form, self.server_url, is_portal)
        self.server_url.setEnabled(is_portal)
        if is_portal:
            self.advanced_toggle.setText("Portal token parameters")
            self.advanced_toggle.setChecked(True)
        else:
            self.advanced_toggle.setText("Advanced standalone parameters")
            self.advanced_toggle.setChecked(False)
        self.advanced_widget.setVisible(self.advanced_toggle.isChecked())
        if self.mode == "portal":
            self.help_text.setPlainText(
                "Federated ArcGIS Server via Portal:\n"
                "1. Sign in against the Portal token URL with username/password.\n"
                "2. If Federated server URL is set, exchange that portal token for a server token using token + serverUrl.\n"
                "Use this when the ArcGIS Server is federated and managed by Portal."
            )
        else:
            self.help_text.setPlainText(
                "ArcGIS Server standalone:\n"
                "Sign in directly against the Server token URL with username/password, expiration and f=json.\n"
                "Client, Referer, IP address, expiration and response format are available under Advanced standalone parameters."
            )

    def on_client_changed(self):
        client_type = self.client_combo.currentData()
        advanced_visible = self.advanced_toggle.isChecked()
        self.client_combo.setEnabled(advanced_visible)
        self.expiration.setEnabled(advanced_visible)
        self.response_format.setEnabled(advanced_visible)
        set_form_row_visible(self.advanced_form, self.referer, advanced_visible and client_type == "referer")
        set_form_row_visible(self.advanced_form, self.ip_address, advanced_visible and client_type == "ip")
        self.referer.setEnabled(advanced_visible and client_type == "referer")
        self.ip_address.setEnabled(advanced_visible and client_type == "ip")

    def get_auth_config(self) -> dict[str, Any]:
        config = dict(self.auth_config)
        config.update(
            {
                "mode": self.mode,
                "token_url": self.token_url.text().strip(),
                "username": self.username.text().strip(),
                "password": self.password.text(),
                "remember_credentials": self.remember_credentials.isChecked(),
                "client": self.client_combo.currentData() or "referer",
                "referer": self.referer.text().strip() or "arcgis-rest-explorer",
                "ip": self.ip_address.text().strip(),
                "server_url": self.server_url.text().strip().rstrip("/"),
                "expiration": self.expiration.value(),
                "response_format": self.response_format.currentData() or "json",
            }
        )
        return config

    @staticmethod
    def mode_label(mode: str) -> str:
        if mode == "portal":
            return "Federated ArcGIS Server via Portal"
        return "ArcGIS Server standalone"

    @staticmethod
    def portal_generate_token_url(portal_url: str) -> str:
        portal_url = portal_url.strip().rstrip("/")
        if portal_url.endswith("/sharing/rest"):
            return portal_url + "/generateToken"
        if portal_url.endswith("/sharing/rest/generateToken"):
            return portal_url
        return portal_url + "/sharing/rest/generateToken"

    @staticmethod
    def default_token_url(default_services_url: str, auth_config: dict[str, Any]) -> str:
        mode = auth_config.get("mode", "server")
        if mode == "portal":
            portal_url = auth_config.get("portal_url", "").strip().rstrip("/")
            if portal_url:
                return GenerateTokenDialog.portal_generate_token_url(portal_url)

        guessed = default_services_url.rstrip("/")
        if "/rest/services" in guessed:
            return guessed.split("/rest/services")[0] + "/tokens/generateToken"
        if not guessed.endswith("generateToken"):
            return guessed.rstrip("/") + "/tokens/generateToken"
        return guessed

    @staticmethod
    def default_server_url(default_services_url: str) -> str:
        guessed = default_services_url.rstrip("/")
        if "/rest/services" in guessed:
            return guessed.split("/rest/services")[0]
        return guessed


class ConnectionAuthDialog(QDialog):
    def __init__(self, services_url: str, auth_config: dict[str, Any] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connection Auth Settings")
        self.resize(760, 440)
        self.services_url = services_url
        self.auth_config = dict(auth_config or {})

        layout = QVBoxLayout(self)
        self.form = QFormLayout()

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Manual / no token generation", "manual")
        self.mode_combo.addItem("ArcGIS Server standalone", "server")
        self.mode_combo.addItem("Portal / federated ArcGIS Server", "portal")

        self.token_url = QLineEdit()
        self.server_url = QLineEdit()
        self.client_combo = QComboBox()
        self.referer = QLineEdit()
        self.ip_address = QLineEdit()
        self.expiration = QSpinBox()
        self.expiration.setRange(1, 20160)
        self.response_format = QComboBox()
        self.response_format.addItem("JSON", "json")
        self.response_format.addItem("Pretty JSON", "pjson")

        self.help_text = QTextEdit()
        self.help_text.setReadOnly(True)
        self.help_text.setMaximumHeight(105)
        self.token_endpoint_label = QLabel("Token endpoint")

        self.form.addRow("Auth mode", self.mode_combo)
        self.form.addRow(self.token_endpoint_label, self.token_url)
        self.form.addRow("Federated server URL", self.server_url)
        layout.addLayout(self.form)

        self.advanced_toggle = QPushButton("Advanced standalone parameters")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.toggled.connect(self.on_advanced_toggled)
        self.advanced_widget = QWidget()
        self.advanced_form = QFormLayout(self.advanced_widget)
        self.advanced_form.setContentsMargins(18, 0, 0, 0)
        self.advanced_form.addRow("Client", self.client_combo)
        self.advanced_form.addRow("Referer", self.referer)
        self.advanced_form.addRow("IP address", self.ip_address)
        self.advanced_form.addRow("Expiration minutes", self.expiration)
        self.advanced_form.addRow("Response format", self.response_format)
        layout.addWidget(self.advanced_toggle)
        layout.addWidget(self.advanced_widget)
        layout.addWidget(QLabel("Notes"))
        layout.addWidget(self.help_text)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.mode_combo.currentIndexChanged.connect(self.on_mode_changed)
        self.client_combo.currentIndexChanged.connect(self.on_client_changed)
        self.load_values()

    def on_advanced_toggled(self, checked: bool):
        self.advanced_widget.setVisible(checked)
        self.on_client_changed()

    def load_values(self):
        mode = self.auth_config.get("mode", "server")
        ix = self.mode_combo.findData(mode)
        self.mode_combo.setCurrentIndex(ix if ix >= 0 else 1)
        self.token_url.setText(self.auth_config.get("token_url") or GenerateTokenDialog.default_token_url(self.services_url, self.auth_config))
        self.server_url.setText(self.auth_config.get("server_url") or GenerateTokenDialog.default_server_url(self.services_url))
        self.configure_client_options(mode, self.auth_config.get("client", "referer"))
        self.referer.setText(self.auth_config.get("referer", "arcgis-rest-explorer"))
        self.ip_address.setText(self.auth_config.get("ip", ""))
        self.expiration.setValue(int(self.auth_config.get("expiration", 60)))
        ix = self.response_format.findData(self.auth_config.get("response_format", "json"))
        self.response_format.setCurrentIndex(ix if ix >= 0 else 0)
        self.on_mode_changed()
        self.on_client_changed()

    def on_mode_changed(self):
        mode = self.mode_combo.currentData()
        current_client = self.client_combo.currentData() or self.auth_config.get("client", "referer")
        self.configure_client_options(mode, str(current_client))
        is_portal = mode == "portal"
        self.token_endpoint_label.setText("Portal token URL" if is_portal else "ArcGIS Server token URL")
        set_form_row_visible(self.form, self.server_url, is_portal)
        self.server_url.setEnabled(is_portal)
        self.token_url.setEnabled(mode != "manual")
        if is_portal:
            self.advanced_toggle.setText("Portal token parameters")
            self.advanced_toggle.setChecked(True)
        else:
            self.advanced_toggle.setText("Advanced standalone parameters")
            self.advanced_toggle.setChecked(False)
        self.advanced_widget.setVisible(self.advanced_toggle.isChecked())
        if mode == "portal" and "/sharing/rest/generateToken" not in self.token_url.text():
            self.token_url.clear()
            self.token_url.setPlaceholderText("https://your-portal.example.com/portal/sharing/rest/generateToken")
        if mode == "server":
            self.help_text.setPlainText(
                "ArcGIS Server standalone uses /tokens/generateToken with username, password, "
                "expiration and f=json. Client, Referer, IP address, expiration and response format "
                "are available under Advanced standalone parameters."
            )
        elif mode == "portal":
            self.help_text.setPlainText(
                "Portal/federated uses the Portal token URL with username/password "
                "to get a portal token. If Federated server URL is set, Generate Token then makes "
                "a second call with token + serverUrl to obtain the server-token for the Connection."
            )
        else:
            self.help_text.setPlainText(
                "No generation endpoint is used. Paste a token manually for this connection if needed; "
                "saving still stores the token in keyring when available."
            )
        self.on_client_changed()

    def configure_client_options(self, mode: str, selected: str):
        self.client_combo.blockSignals(True)
        self.client_combo.clear()
        self.client_combo.addItem("Referer", "referer")
        self.client_combo.addItem("IP address", "ip")
        if mode == "server":
            self.client_combo.addItem("Request IP", "requestip")
        ix = self.client_combo.findData(selected)
        self.client_combo.setCurrentIndex(ix if ix >= 0 else 0)
        self.client_combo.blockSignals(False)

    def on_client_changed(self):
        client_type = self.client_combo.currentData()
        advanced_visible = self.advanced_toggle.isChecked()
        self.client_combo.setEnabled(advanced_visible)
        self.expiration.setEnabled(advanced_visible)
        self.response_format.setEnabled(advanced_visible)
        set_form_row_visible(self.advanced_form, self.referer, advanced_visible and client_type == "referer")
        set_form_row_visible(self.advanced_form, self.ip_address, advanced_visible and client_type == "ip")
        self.referer.setEnabled(advanced_visible and client_type == "referer")
        self.ip_address.setEnabled(advanced_visible and client_type == "ip")

    def get_auth_config(self) -> dict[str, Any]:
        mode = self.mode_combo.currentData()
        return {
            "mode": mode,
            "token_url": self.token_url.text().strip(),
            "server_url": self.server_url.text().strip().rstrip("/"),
            "client": self.client_combo.currentData() or "referer",
            "referer": self.referer.text().strip() or "arcgis-rest-explorer",
            "ip": self.ip_address.text().strip(),
            "expiration": self.expiration.value(),
            "response_format": self.response_format.currentData() or "json",
        }


class GeometryLabDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Geometry Lab")
        self.resize(850, 650)
        self.parsed_geojson_features: list[dict[str, Any]] = []
        self.arcgis_geometry: dict[str, Any] | None = None
        self.arcgis_geometry_type: str | None = None

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.format_combo = QComboBox()
        self.format_combo.addItems(["Auto", "GeoJSON", "ArcGIS JSON", "WKT"])
        form.addRow("Input format", self.format_combo)
        layout.addLayout(form)

        self.input_text = QTextEdit()
        self.input_text.setPlaceholderText(
            "Paste GeoJSON, ArcGIS JSON geometry/feature, or WKT.\n\n"
            "Examples:\n"
            "POINT (12.4924 41.8902)\n"
            "LINESTRING (12.49 41.89, 12.50 41.90)\n"
            "POLYGON ((12.49 41.89, 12.50 41.89, 12.50 41.90, 12.49 41.89))\n"
            '{"x":12.4924,"y":41.8902,"spatialReference":{"wkid":4326}}'
        )
        layout.addWidget(QLabel("Geometry input"))
        layout.addWidget(self.input_text, 3)

        buttons = QHBoxLayout()
        self.parse_btn = QPushButton("Parse / Preview")
        self.parse_btn.clicked.connect(self.parse_input)
        self.sample_point_btn = QPushButton("Sample Point")
        self.sample_point_btn.clicked.connect(self.load_sample_point)
        self.sample_polygon_btn = QPushButton("Sample Polygon")
        self.sample_polygon_btn.clicked.connect(self.load_sample_polygon)
        buttons.addWidget(self.parse_btn)
        buttons.addWidget(self.sample_point_btn)
        buttons.addWidget(self.sample_polygon_btn)
        layout.addLayout(buttons)

        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        layout.addWidget(QLabel("ArcGIS query params / conversion output"))
        layout.addWidget(self.output_text, 2)

        dialog_buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        dialog_buttons.button(QDialogButtonBox.Ok).setText("Use as Spatial Filter")
        dialog_buttons.accepted.connect(self.accept)
        dialog_buttons.rejected.connect(self.reject)
        layout.addWidget(dialog_buttons)

    def load_sample_point(self):
        self.format_combo.setCurrentText("WKT")
        self.input_text.setPlainText("POINT (12.4924 41.8902)")

    def load_sample_polygon(self):
        self.format_combo.setCurrentText("WKT")
        self.input_text.setPlainText("POLYGON ((12.485 41.887, 12.501 41.887, 12.501 41.895, 12.485 41.895, 12.485 41.887))")

    def parse_input(self):
        text = self.input_text.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Empty geometry", "Paste a geometry first.")
            return
        try:
            geojson_geometry = self.parse_geometry(text, self.format_combo.currentText())
            self.arcgis_geometry, self.arcgis_geometry_type = self.geojson_geometry_to_arcgis(geojson_geometry)
            self.parsed_geojson_features = [{
                "type": "Feature",
                "geometry": geojson_geometry,
                "properties": {"__featureIndex": 0, "source": "Geometry Lab", "geometryType": self.arcgis_geometry_type},
            }]
            params = {
                "geometry": self.arcgis_geometry,
                "geometryType": self.arcgis_geometry_type,
                "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects",
            }
            self.output_text.setPlainText(json.dumps(params, indent=2, ensure_ascii=False))
        except Exception as exc:
            QMessageBox.critical(self, "Geometry parse error", str(exc))

    def parse_geometry(self, text: str, fmt: str) -> dict[str, Any]:
        return geom_utils.parse_geometry(text, fmt)

    def extract_geojson_geometry(self, data: dict[str, Any]) -> dict[str, Any]:
        return geom_utils.extract_geojson_geometry(data)

    def arcgis_json_to_geojson_geometry(self, data: dict[str, Any]) -> dict[str, Any]:
        return geom_utils.arcgis_json_to_geojson_geometry(data)

    def parse_wkt(self, text: str) -> dict[str, Any]:
        return geom_utils.parse_wkt(text)

    @staticmethod
    def parse_pair(text: str) -> tuple[float, float]:
        return geom_utils.parse_pair(text)

    def geojson_geometry_to_arcgis(self, geom: dict[str, Any]) -> tuple[dict[str, Any], str]:
        return geom_utils.geojson_geometry_to_arcgis(geom)
