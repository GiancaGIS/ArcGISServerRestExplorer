import json
from pathlib import Path
from typing import Any


MAP_PREVIEW_JS_URL = Path(__file__).with_name("map_preview.js").resolve().as_uri()


def script_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def build_leaflet_map_html(
    geojson_features: list[dict[str, Any]],
    basemap: dict[str, str],
    renderer_style: dict[str, Any],
    ui_theme: str = "Dark",
) -> str:
    feature_collection = {"type": "FeatureCollection", "features": geojson_features}
    config_text = script_json(
        {
            "data": feature_collection,
            "rendererStyle": renderer_style,
            "basemapUrl": basemap["url"],
            "attribution": basemap["attribution"],
        }
    )
    script_url = script_json(MAP_PREVIEW_JS_URL)
    is_light = ui_theme == "Light"
    page_bg = "#f8fafc" if is_light else "#0f1720"
    popup_bg = "#ffffff" if is_light else "#111827"
    popup_fg = "#0f172a" if is_light else "#e5e7eb"
    popup_border = "#cbd5e1" if is_light else "#374151"
    popup_accent = "#2563eb" if is_light else "#93c5fd"

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<title>Map Preview</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<style>
html, body, #map {{ height: 100%; margin: 0; background: {page_bg}; }}
.leaflet-popup-content-wrapper, .leaflet-popup-tip {{ background: {popup_bg}; color: {popup_fg}; }}
.popup-title {{ font-weight: 800; color: {popup_accent}; margin-bottom: 6px; }}
.popup-table {{ border-collapse: collapse; font-size: 12px; }}
.popup-table td {{ border-bottom: 1px solid {popup_border}; padding: 3px 6px; vertical-align: top; }}
.popup-table td:first-child {{ color: {popup_accent}; font-weight: 700; }}
.draw-hint {{
    position: absolute; z-index: 1000; top: 12px; left: 50%; transform: translateX(-50%);
    background: rgba(15, 23, 42, 0.88); color: #f8fafc; padding: 8px 12px;
    border-radius: 8px; font: 13px sans-serif; display: none;
}}
.draw-controls {{
    position: absolute; z-index: 1000; top: 52px; left: 50%; transform: translateX(-50%);
    display: none; gap: 8px;
}}
.draw-controls button {{
    background: #f97316; color: #fff7ed; border: 0; border-radius: 8px;
    padding: 7px 12px; font: 700 12px sans-serif; cursor: pointer;
    box-shadow: 0 8px 20px rgba(15, 23, 42, 0.25);
}}
.draw-controls button.secondary {{ background: #334155; color: #f8fafc; }}
</style>
</head>
<body>
<div id="map"></div>
<div id="drawHint" class="draw-hint">Draw mode active</div>
<div id="drawControls" class="draw-controls">
  <button id="finishPolygonBtn" type="button">Finish Polygon</button>
  <button id="cancelPolygonBtn" class="secondary" type="button">Cancel</button>
</div>
<script>
window.arcgisRestExplorerMapConfig = {config_text};
</script>
<script src={script_url}></script>
</body>
</html>
"""
