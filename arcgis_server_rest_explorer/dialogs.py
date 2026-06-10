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
)

from . import arcgis_geometry as geom_utils


class GenerateTokenDialog(QDialog):
    def __init__(self, default_services_url: str, auth_config: dict[str, Any] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Generate ArcGIS Token")
        self.resize(760, 520)
        self.generated_token = ""
        self.expires_text = ""
        self.auth_config = auth_config or {}
        self.mode = self.auth_config.get("mode", "server")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        token_url = self.auth_config.get("token_url") or self.default_token_url(default_services_url, self.auth_config)

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
        self.result = QTextEdit()
        self.result.setReadOnly(True)
        self.result.setMaximumHeight(120)
        self.help_text = QTextEdit()
        self.help_text.setReadOnly(True)
        self.help_text.setMaximumHeight(85)

        self.auth_mode_label = QLabel(f"Connection auth mode: {self.mode}")
        self.configure_client_options(self.mode, self.auth_config.get("client", "referer"))
        self.client_combo.currentIndexChanged.connect(self.on_client_changed)
        form.addRow("Mode", self.auth_mode_label)
        form.addRow("Token endpoint", self.token_url)
        form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        form.addRow("", self.remember_credentials)
        form.addRow("Client", self.client_combo)
        form.addRow("Referer", self.referer)
        form.addRow("IP address", self.ip_address)
        form.addRow("Federated server URL", self.server_url)
        form.addRow("Expiration minutes", self.expiration)
        layout.addLayout(form)
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
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
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
            token_type = "Federated server token" if exchanged_for_server else "Token"
            self.result.setPlainText(f"{token_type} generated.\nExpires: {self.expires_text or 'unknown'}\nPreview: {preview}")

        except Exception as exc:
            QMessageBox.critical(self, "Token error", str(exc))

    def build_credentials_payload(self) -> dict[str, str] | None:
        client_type = self.client_combo.currentData() or "referer"
        payload = {
            "username": self.username.text().strip(),
            "password": self.password.text(),
            "client": client_type,
            "expiration": str(self.expiration.value()),
            "f": "json",
        }
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
        self.server_url.setEnabled(self.mode == "portal")
        if self.mode == "portal":
            self.help_text.setPlainText(
                "Portal/federated: POST username/password to Portal generateToken. "
                "If Federated server URL is set, a second POST exchanges the portal token with "
                "token + serverUrl to obtain the server-token used by the Connection."
            )
        else:
            self.help_text.setPlainText(
                "ArcGIS Server standalone: POST username/password directly to /tokens/generateToken. "
                "Portal URL and federated serverUrl are not part of this REST call."
            )

    def on_client_changed(self):
        client_type = self.client_combo.currentData()
        self.referer.setEnabled(client_type == "referer")
        self.ip_address.setEnabled(client_type == "ip")

    def get_auth_config(self) -> dict[str, Any]:
        config = dict(self.auth_config)
        config.update(
            {
                "token_url": self.token_url.text().strip(),
                "username": self.username.text().strip(),
                "password": self.password.text(),
                "remember_credentials": self.remember_credentials.isChecked(),
                "client": self.client_combo.currentData() or "referer",
                "referer": self.referer.text().strip() or "arcgis-rest-explorer",
                "ip": self.ip_address.text().strip(),
                "server_url": self.server_url.text().strip().rstrip("/"),
                "expiration": self.expiration.value(),
            }
        )
        return config

    @staticmethod
    def default_token_url(default_services_url: str, auth_config: dict[str, Any]) -> str:
        mode = auth_config.get("mode", "server")
        if mode == "portal":
            portal_url = auth_config.get("portal_url", "").strip().rstrip("/")
            if portal_url:
                if portal_url.endswith("/sharing/rest"):
                    return portal_url + "/generateToken"
                return portal_url + "/sharing/rest/generateToken"

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
        form = QFormLayout()

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Manual / no token generation", "manual")
        self.mode_combo.addItem("ArcGIS Server standalone", "server")
        self.mode_combo.addItem("Portal / federated ArcGIS Server", "portal")

        self.token_url = QLineEdit()
        self.portal_url = QLineEdit()
        self.server_url = QLineEdit()
        self.client_combo = QComboBox()
        self.referer = QLineEdit()
        self.ip_address = QLineEdit()
        self.expiration = QSpinBox()
        self.expiration.setRange(1, 20160)

        self.help_text = QTextEdit()
        self.help_text.setReadOnly(True)
        self.help_text.setMaximumHeight(105)

        form.addRow("Auth mode", self.mode_combo)
        form.addRow("Token endpoint", self.token_url)
        form.addRow("Portal URL", self.portal_url)
        form.addRow("Federated server URL", self.server_url)
        form.addRow("Client", self.client_combo)
        form.addRow("Referer", self.referer)
        form.addRow("IP address", self.ip_address)
        form.addRow("Expiration minutes", self.expiration)
        layout.addLayout(form)
        layout.addWidget(QLabel("Notes"))
        layout.addWidget(self.help_text)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.mode_combo.currentIndexChanged.connect(self.on_mode_changed)
        self.client_combo.currentIndexChanged.connect(self.on_client_changed)
        self.load_values()

    def load_values(self):
        mode = self.auth_config.get("mode", "server")
        ix = self.mode_combo.findData(mode)
        self.mode_combo.setCurrentIndex(ix if ix >= 0 else 1)
        self.token_url.setText(self.auth_config.get("token_url") or GenerateTokenDialog.default_token_url(self.services_url, self.auth_config))
        self.portal_url.setText(self.auth_config.get("portal_url", ""))
        self.server_url.setText(self.auth_config.get("server_url") or GenerateTokenDialog.default_server_url(self.services_url))
        self.configure_client_options(mode, self.auth_config.get("client", "referer"))
        self.referer.setText(self.auth_config.get("referer", "arcgis-rest-explorer"))
        self.ip_address.setText(self.auth_config.get("ip", ""))
        self.expiration.setValue(int(self.auth_config.get("expiration", 60)))
        self.on_mode_changed()
        self.on_client_changed()

    def on_mode_changed(self):
        mode = self.mode_combo.currentData()
        current_client = self.client_combo.currentData() or self.auth_config.get("client", "referer")
        self.configure_client_options(mode, str(current_client))
        self.portal_url.setEnabled(mode == "portal")
        self.server_url.setEnabled(mode == "portal")
        self.token_url.setEnabled(mode != "manual")
        self.client_combo.setEnabled(mode != "manual")
        self.referer.setEnabled(mode != "manual")
        self.ip_address.setEnabled(mode != "manual")
        self.expiration.setEnabled(mode != "manual")
        if mode == "portal" and not self.portal_url.text().strip():
            self.portal_url.setPlaceholderText("https://your-portal.example.com/portal")
        if mode == "server":
            self.help_text.setPlainText(
                "ArcGIS Server standalone uses /tokens/generateToken with username, password, "
                "client, referer or ip, expiration and f=json. Portal URL and Federated server URL "
                "are disabled because they are not request parameters for standalone Server."
            )
        elif mode == "portal":
            self.help_text.setPlainText(
                "Portal/federated uses Portal /sharing/rest/generateToken with username/password "
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
        mode = self.mode_combo.currentData()
        client_type = self.client_combo.currentData()
        enabled = mode != "manual"
        self.referer.setEnabled(enabled and client_type == "referer")
        self.ip_address.setEnabled(enabled and client_type == "ip")

    def get_auth_config(self) -> dict[str, Any]:
        mode = self.mode_combo.currentData()
        return {
            "mode": mode,
            "token_url": self.token_url.text().strip(),
            "portal_url": self.portal_url.text().strip(),
            "server_url": self.server_url.text().strip().rstrip("/"),
            "client": self.client_combo.currentData() or "referer",
            "referer": self.referer.text().strip() or "arcgis-rest-explorer",
            "ip": self.ip_address.text().strip(),
            "expiration": self.expiration.value(),
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


