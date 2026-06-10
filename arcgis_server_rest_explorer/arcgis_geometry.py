import math
from typing import Any


WEBMERCATOR_WKIDS = {3857, 102100, 102113}


def parse_geometry(text: str, fmt: str) -> dict[str, Any]:
    if fmt == "WKT" or (fmt == "Auto" and text[:1].isalpha()):
        return parse_wkt(text)
    data = _json_loads(text)
    if fmt == "GeoJSON" or (
        fmt == "Auto"
        and data.get("type") in ("Feature", "FeatureCollection", "Point", "LineString", "Polygon", "MultiLineString", "MultiPolygon")
    ):
        return extract_geojson_geometry(data)
    return arcgis_json_to_geojson_geometry(data)


def _json_loads(text: str) -> dict[str, Any]:
    import json

    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    return data


def extract_geojson_geometry(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("type") == "FeatureCollection":
        features = data.get("features") or []
        if not features:
            raise ValueError("GeoJSON FeatureCollection is empty.")
        return extract_geojson_geometry(features[0])
    if data.get("type") == "Feature":
        geom = data.get("geometry")
        if not geom:
            raise ValueError("GeoJSON Feature has no geometry.")
        return geom
    if data.get("type") in ("Point", "LineString", "Polygon", "MultiLineString", "MultiPolygon"):
        return data
    raise ValueError("Unsupported GeoJSON geometry.")


def arcgis_json_to_geojson_geometry(data: dict[str, Any]) -> dict[str, Any]:
    if "geometry" in data and isinstance(data["geometry"], dict):
        data = data["geometry"]
    if "x" in data and "y" in data:
        return {"type": "Point", "coordinates": [float(data["x"]), float(data["y"])]}
    if "paths" in data:
        paths = data["paths"]
        if len(paths) == 1:
            return {"type": "LineString", "coordinates": paths[0]}
        return {"type": "MultiLineString", "coordinates": paths}
    if "rings" in data:
        return {"type": "Polygon", "coordinates": data["rings"]}
    raise ValueError("Unsupported ArcGIS JSON geometry. Expected x/y, paths or rings.")


def parse_wkt(text: str) -> dict[str, Any]:
    clean = " ".join(text.strip().split())
    upper = clean.upper()
    if upper.startswith("POINT"):
        inside = clean[clean.find("(") + 1 : clean.rfind(")")]
        x, y = parse_pair(inside)
        return {"type": "Point", "coordinates": [x, y]}
    if upper.startswith("LINESTRING"):
        inside = clean[clean.find("(") + 1 : clean.rfind(")")]
        coords = [list(parse_pair(part)) for part in inside.split(",")]
        return {"type": "LineString", "coordinates": coords}
    if upper.startswith("POLYGON"):
        inside = clean[clean.find("((") + 2 : clean.rfind("))")]
        ring = [list(parse_pair(part)) for part in inside.split(",")]
        return {"type": "Polygon", "coordinates": [ring]}
    raise ValueError("Supported WKT: POINT, LINESTRING, POLYGON single ring.")


def parse_pair(text: str) -> tuple[float, float]:
    parts = text.strip().split()
    if len(parts) < 2:
        raise ValueError(f"Invalid coordinate pair: {text}")
    return float(parts[0]), float(parts[1])


def geojson_geometry_to_arcgis(geom: dict[str, Any]) -> tuple[dict[str, Any], str]:
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    sr = {"wkid": 4326}
    if gtype == "Point":
        return {"x": coords[0], "y": coords[1], "spatialReference": sr}, "esriGeometryPoint"
    if gtype == "LineString":
        return {"paths": [coords], "spatialReference": sr}, "esriGeometryPolyline"
    if gtype == "MultiLineString":
        return {"paths": coords, "spatialReference": sr}, "esriGeometryPolyline"
    if gtype == "Polygon":
        return {"rings": coords, "spatialReference": sr}, "esriGeometryPolygon"
    if gtype == "MultiPolygon":
        rings = []
        for polygon in coords:
            rings.extend(polygon)
        return {"rings": rings, "spatialReference": sr}, "esriGeometryPolygon"
    raise ValueError(f"Unsupported geometry type: {gtype}")


def arcgis_geometry_to_geojson_geometry(geometry: dict[str, Any], wkid: int | None) -> dict[str, Any] | None:
    if "x" in geometry and "y" in geometry:
        lon, lat = to_lon_lat(geometry["x"], geometry["y"], wkid)
        return {"type": "Point", "coordinates": [lon, lat]}

    if "paths" in geometry:
        paths = [[list(to_lon_lat(x, y, wkid)) for x, y in path] for path in geometry["paths"]]
        return {"type": "LineString", "coordinates": paths[0]} if len(paths) == 1 else {"type": "MultiLineString", "coordinates": paths}

    if "rings" in geometry:
        rings = [[list(to_lon_lat(x, y, wkid)) for x, y in ring] for ring in geometry["rings"]]
        return {"type": "Polygon", "coordinates": rings}
    return None


def to_lon_lat(x: float, y: float, wkid: int | None) -> tuple[float, float]:
    if wkid in WEBMERCATOR_WKIDS:
        return webmercator_to_wgs84(x, y)
    return float(x), float(y)


def webmercator_to_wgs84(x: float, y: float) -> tuple[float, float]:
    lon = (float(x) / 20037508.34) * 180.0
    lat = (float(y) / 20037508.34) * 180.0
    lat = 180.0 / math.pi * (2.0 * math.atan(math.exp(lat * math.pi / 180.0)) - math.pi / 2.0)
    return lon, lat
