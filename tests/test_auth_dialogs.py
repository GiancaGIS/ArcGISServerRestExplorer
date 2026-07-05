import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from arcgis_server_rest_explorer.dialogs import ConnectionAuthDialog, GenerateTokenDialog


def qt_app():
    return QApplication.instance() or QApplication([])


def test_standalone_server_token_url_is_derived_from_services_url():
    url = GenerateTokenDialog.default_token_url(
        "https://server.example.com/arcgis/rest/services",
        {"mode": "server"},
    )

    assert url == "https://server.example.com/arcgis/tokens/generateToken"


def test_portal_token_url_is_derived_from_portal_url():
    url = GenerateTokenDialog.default_token_url(
        "https://server.example.com/server/rest/services",
        {"mode": "portal", "portal_url": "https://portal.example.com/portal"},
    )

    assert url == "https://portal.example.com/portal/sharing/rest/generateToken"


def test_portal_token_url_accepts_sharing_rest_url():
    url = GenerateTokenDialog.portal_generate_token_url(
        "https://portal.example.com/portal/sharing/rest"
    )

    assert url == "https://portal.example.com/portal/sharing/rest/generateToken"


def test_generate_token_dialog_hides_federated_fields_for_standalone_server():
    qt_app()
    dialog = GenerateTokenDialog(
        "https://server.example.com/arcgis/rest/services",
        {"mode": "server"},
    )

    assert not dialog.server_url.isVisibleTo(dialog)
    assert not hasattr(dialog, "portal_url")
    assert not dialog.client_combo.isVisibleTo(dialog)
    assert not dialog.referer.isVisibleTo(dialog)
    assert not dialog.ip_address.isVisibleTo(dialog)


def test_generate_token_dialog_standalone_payload_omits_client_fields():
    qt_app()
    dialog = GenerateTokenDialog(
        "https://server.example.com/arcgis/rest/services",
        {"mode": "server"},
    )
    dialog.username.setText("user")
    dialog.password.setText("password")

    payload = dialog.build_credentials_payload()

    assert payload is not None
    assert "client" not in payload
    assert "referer" not in payload
    assert "ip" not in payload


def test_generate_token_dialog_keeps_ssl_verify_setting():
    qt_app()
    dialog = GenerateTokenDialog(
        "https://server.example.com/arcgis/rest/services",
        {"mode": "server"},
        verify_ssl=False,
    )

    assert dialog.verify_ssl is False


def test_generate_token_dialog_standalone_advanced_payload_adds_documented_client_fields():
    qt_app()
    dialog = GenerateTokenDialog(
        "https://server.example.com/arcgis/rest/services",
        {"mode": "server"},
    )
    dialog.username.setText("user")
    dialog.password.setText("password")
    dialog.advanced_toggle.setChecked(True)
    dialog.client_combo.setCurrentIndex(dialog.client_combo.findData("referer"))
    dialog.referer.setText("https://example.com/app")
    dialog.response_format.setCurrentIndex(dialog.response_format.findData("pjson"))

    payload = dialog.build_credentials_payload()

    assert payload is not None
    assert payload["client"] == "referer"
    assert payload["referer"] == "https://example.com/app"
    assert payload["expiration"] == "60"
    assert payload["f"] == "pjson"


def test_generate_token_dialog_standalone_advanced_requestip_omits_referer_and_ip():
    qt_app()
    dialog = GenerateTokenDialog(
        "https://server.example.com/arcgis/rest/services",
        {"mode": "server"},
    )
    dialog.username.setText("user")
    dialog.password.setText("password")
    dialog.advanced_toggle.setChecked(True)
    dialog.client_combo.setCurrentIndex(dialog.client_combo.findData("requestip"))

    payload = dialog.build_credentials_payload()

    assert payload is not None
    assert payload["client"] == "requestip"
    assert "referer" not in payload
    assert "ip" not in payload


def test_generate_token_dialog_shows_federated_fields_for_portal():
    qt_app()
    dialog = GenerateTokenDialog(
        "https://server.example.com/server/rest/services",
        {
            "mode": "portal",
            "token_url": "https://portal.example.com/portal/sharing/rest/generateToken",
        },
    )

    assert not hasattr(dialog, "portal_url")
    assert dialog.token_endpoint_label.text() == "Portal token URL"
    assert dialog.token_url.isVisibleTo(dialog)
    assert dialog.server_url.isVisibleTo(dialog)
    assert dialog.client_combo.isVisibleTo(dialog)
    assert dialog.advanced_toggle.isChecked()


def test_connection_auth_dialog_hides_federated_fields_for_standalone_server():
    qt_app()
    dialog = ConnectionAuthDialog(
        "https://server.example.com/arcgis/rest/services",
        {"mode": "server"},
    )

    assert not dialog.server_url.isVisibleTo(dialog)
    assert not hasattr(dialog, "portal_url")
    assert not dialog.client_combo.isVisibleTo(dialog)
    assert not dialog.referer.isVisibleTo(dialog)
    assert not dialog.ip_address.isVisibleTo(dialog)
    assert not dialog.advanced_toggle.isChecked()


def test_connection_auth_dialog_uses_token_endpoint_for_portal():
    qt_app()
    dialog = ConnectionAuthDialog(
        "https://server.example.com/server/rest/services",
        {
            "mode": "portal",
            "token_url": "https://portal.example.com/portal/sharing/rest/generateToken",
        },
    )

    assert not hasattr(dialog, "portal_url")
    assert dialog.token_endpoint_label.text() == "Portal token URL"
    assert dialog.token_url.text() == "https://portal.example.com/portal/sharing/rest/generateToken"
    assert dialog.server_url.isVisibleTo(dialog)
