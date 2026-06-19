from typing import Any


MAP_STYLE_PRESETS: dict[str, dict[str, Any]] = {
    "ArcGIS renderer": {},
    "Blue solid": {
        "color": "#1d4ed8",
        "fillColor": "#60a5fa",
        "weight": 2,
        "opacity": 0.95,
        "fillOpacity": 0.35,
        "radius": 7,
    },
    "Orange focus": {
        "color": "#ea580c",
        "fillColor": "#fb923c",
        "weight": 3,
        "opacity": 1.0,
        "fillOpacity": 0.28,
        "radius": 8,
    },
    "Emerald light": {
        "color": "#047857",
        "fillColor": "#34d399",
        "weight": 2,
        "opacity": 0.95,
        "fillOpacity": 0.30,
        "radius": 7,
    },
    "High contrast": {
        "color": "#facc15",
        "fillColor": "#000000",
        "weight": 4,
        "opacity": 1.0,
        "fillOpacity": 0.15,
        "radius": 9,
    },
    "Hollow outline": {
        "color": "#ef4444",
        "fillColor": "#ef4444",
        "weight": 3,
        "opacity": 1.0,
        "fillOpacity": 0.0,
        "radius": 8,
    },
}


def arcgis_color_to_hex(color: list[int]) -> str:
    r, g, b = color[:3]
    return f"#{int(r):02x}{int(g):02x}{int(b):02x}"


def build_leaflet_style_from_renderer(metadata: dict[str, Any] | None, preset_name: str) -> dict[str, Any]:
    meta = metadata or {}
    renderer = meta.get("drawingInfo", {}).get("renderer", {})
    style = {
        "color": "#60a5fa",
        "fillColor": "#60a5fa",
        "weight": 2,
        "opacity": 0.95,
        "fillOpacity": 0.30,
        "radius": 7,
        "rendererType": renderer.get("type") if isinstance(renderer, dict) else None,
    }
    preset = MAP_STYLE_PRESETS.get(preset_name, {})
    if preset:
        return {**style, **preset, "rendererType": f"preset:{preset_name}"}
    if not isinstance(renderer, dict):
        return style
    symbol = renderer.get("symbol")
    if not isinstance(symbol, dict):
        return style

    color = symbol.get("color")
    outline = symbol.get("outline", {})
    outline_color = outline.get("color") if isinstance(outline, dict) else None

    if isinstance(color, list) and len(color) >= 3:
        style["fillColor"] = arcgis_color_to_hex(color)
        if len(color) >= 4:
            style["fillOpacity"] = round(color[3] / 255, 2)
    if isinstance(outline_color, list) and len(outline_color) >= 3:
        style["color"] = arcgis_color_to_hex(outline_color)
    if symbol.get("size"):
        try:
            style["radius"] = max(4, float(symbol["size"]) / 2)
        except Exception:
            pass
    if isinstance(outline, dict) and outline.get("width"):
        try:
            style["weight"] = max(1, float(outline["width"]))
        except Exception:
            pass
    return style
