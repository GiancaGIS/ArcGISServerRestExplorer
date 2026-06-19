﻿from importlib.resources import files

from arcgis_server_rest_explorer.map_html import build_leaflet_map_html, script_json


def read_map_preview_js():
    return files("arcgis_server_rest_explorer").joinpath("map_preview.js").read_text(encoding="utf-8")


def test_script_json_does_not_close_script_tag():
    assert "</script>" not in script_json({"value": "</script>"})


def test_map_html_imports_external_preview_script():
    html = build_leaflet_map_html(
        [],
        {"url": "https://example.com/{z}/{x}/{y}.png", "attribution": "Tiles"},
        {},
    )

    assert "window.arcgisRestExplorerMapConfig" in html
    assert "map_preview.js" in html
    assert "function escapeHtml" not in html


def test_map_html_serializes_basemap_zoom_limits():
    html = build_leaflet_map_html(
        [],
        {
            "url": "https://example.com/{z}/{x}/{y}.png",
            "attribution": "Tiles",
            "maxNativeZoom": 13,
        },
        {},
    )

    assert '"basemapMaxZoom": 19' in html
    assert '"basemapMaxNativeZoom": 13' in html


def test_map_html_serializes_google_basemap_config():
    html = build_leaflet_map_html(
        [],
        {
            "provider": "google",
            "googleMapType": "satellite",
            "googleApiKey": "test-key",
            "attribution": "Map data &copy; Google",
            "maxZoom": 22,
        },
        {},
    )

    assert '"basemapProvider": "google"' in html
    assert '"googleMapType": "satellite"' in html
    assert '"googleApiKey": "test-key"' in html


def test_map_preview_js_escapes_popup_values():
    js = read_map_preview_js()

    assert "function escapeHtml" in js
    assert "popupHtml" in js
    assert "&lt;" in js


def test_map_preview_js_exposes_area_drawing_hook():
    js = read_map_preview_js()

    assert "window.enableAreaDrawing" in js
    assert "bridge.onAreaDrawn" in js
    assert "window.enablePolygonDrawing" in js
    assert "bridge.onPolygonDrawn(JSON.stringify(coords))" in js
    assert "Finish Polygon" in build_leaflet_map_html(
        [],
        {"url": "https://example.com/{z}/{x}/{y}.png", "attribution": "Tiles"},
        {},
    )
    assert "finishPolygonDrawing" in js


def test_map_preview_js_supports_google_map_tiles_api():
    js = read_map_preview_js()

    assert "addGoogleBasemapLayer" in js
    assert "https://tile.googleapis.com/v1/createSession" in js
    assert "https://tile.googleapis.com/v1/2dtiles/{z}/{x}/{y}" in js
