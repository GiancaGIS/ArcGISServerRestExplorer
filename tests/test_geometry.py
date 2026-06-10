import math

from arcgis_server_rest_explorer import arcgis_geometry as geom


def test_parse_wkt_point():
    assert geom.parse_wkt("POINT (12.4924 41.8902)") == {
        "type": "Point",
        "coordinates": [12.4924, 41.8902],
    }


def test_arcgis_point_to_geojson():
    assert geom.arcgis_json_to_geojson_geometry({"x": 12, "y": 41}) == {
        "type": "Point",
        "coordinates": [12.0, 41.0],
    }


def test_webmercator_to_wgs84():
    lon, lat = geom.webmercator_to_wgs84(1390647.6067858906, 5144546.1003622655)
    assert math.isclose(lon, 12.4924, abs_tol=0.001)
    assert math.isclose(lat, 41.8902, abs_tol=0.001)


def test_geojson_polygon_to_arcgis():
    geometry, geometry_type = geom.geojson_geometry_to_arcgis(
        {"type": "Polygon", "coordinates": [[[12, 41], [13, 41], [12, 41]]]}
    )
    assert geometry_type == "esriGeometryPolygon"
    assert geometry["spatialReference"] == {"wkid": 4326}
    assert geometry["rings"][0][0] == [12, 41]
