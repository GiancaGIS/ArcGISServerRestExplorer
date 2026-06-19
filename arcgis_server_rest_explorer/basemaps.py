from typing import Any


BASEMAPS: dict[str, dict[str, Any]] = {
    "OpenStreetMap": {
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": "&copy; OpenStreetMap contributors",
    },
    "ESRI World Imagery": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Tiles &copy; Esri",
    },
    "ESRI Streets": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Tiles &copy; Esri",
    },
    "ESRI Topographic": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Tiles &copy; Esri",
    },
    "ESRI Terrain": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Tiles &copy; Esri",
        "maxNativeZoom": 13,
    },
    "ESRI Oceans": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Tiles &copy; Esri",
        "maxNativeZoom": 10,
    },
    "ESRI National Geographic": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/NatGeo_World_Map/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Tiles &copy; Esri",
        "maxNativeZoom": 16,
    },
    "ESRI Shaded Relief": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Shaded_Relief/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Tiles &copy; Esri",
        "maxNativeZoom": 13,
    },
    "ESRI Dark Gray": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Tiles &copy; Esri",
    },
    "ESRI Light Gray": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Tiles &copy; Esri",
    },
    "Google Roadmap": {
        "provider": "google",
        "googleMapType": "roadmap",
        "attribution": "Map data &copy; Google",
        "maxZoom": 22,
    },
    "Google Satellite": {
        "provider": "google",
        "googleMapType": "satellite",
        "attribution": "Map data &copy; Google",
        "maxZoom": 22,
    },
    "Google Terrain": {
        "provider": "google",
        "googleMapType": "terrain",
        "attribution": "Map data &copy; Google",
        "maxZoom": 22,
    },
}
