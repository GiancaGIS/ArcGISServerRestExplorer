from arcgis_server_rest_explorer.map_styles import arcgis_color_to_hex, build_leaflet_style_from_renderer


def test_arcgis_renderer_style_uses_symbol_color_and_outline():
    style = build_leaflet_style_from_renderer(
        {
            "drawingInfo": {
                "renderer": {
                    "type": "simple",
                    "symbol": {
                        "color": [255, 0, 128, 128],
                        "outline": {"color": [0, 64, 255, 255], "width": 3},
                        "size": 12,
                    },
                }
            }
        },
        "ArcGIS renderer",
    )

    assert style["fillColor"] == "#ff0080"
    assert style["color"] == "#0040ff"
    assert style["fillOpacity"] == 0.5
    assert style["weight"] == 3
    assert style["radius"] == 6


def test_map_style_preset_overrides_renderer():
    style = build_leaflet_style_from_renderer({}, "Hollow outline")

    assert style["color"] == "#ef4444"
    assert style["fillOpacity"] == 0.0
    assert style["rendererType"] == "preset:Hollow outline"


def test_arcgis_color_to_hex_formats_rgb_triplet():
    assert arcgis_color_to_hex([1, 16, 255, 128]) == "#0110ff"
