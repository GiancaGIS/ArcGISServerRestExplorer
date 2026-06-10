from arcgis_server_rest_explorer.query_utils import build_query_params


def test_query_params_include_outsr_when_geometry_is_returned():
    params = build_query_params("1=1", "*", True, "100")
    assert params["outSR"] == "4326"


def test_query_params_omit_outsr_without_geometry():
    params = build_query_params("1=1", "*", False, "100")
    assert "outSR" not in params


def test_spatial_filter_is_serialized():
    params = build_query_params(
        "1=1",
        "*",
        True,
        "100",
        spatial_filter_geometry={"x": 12, "y": 41, "spatialReference": {"wkid": 4326}},
        spatial_filter_geometry_type="esriGeometryPoint",
    )
    assert params["geometryType"] == "esriGeometryPoint"
    assert '"wkid": 4326' in params["geometry"]


def test_spatial_filter_uses_selected_relation():
    params = build_query_params(
        "1=1",
        "*",
        True,
        "100",
        spatial_filter_geometry={"x": 12, "y": 41, "spatialReference": {"wkid": 4326}},
        spatial_filter_geometry_type="esriGeometryPoint",
        spatial_rel="esriSpatialRelWithin",
    )
    assert params["spatialRel"] == "esriSpatialRelWithin"
