from typing import Any


def build_query_params(
    where: str,
    out_fields: str,
    return_geometry: bool,
    max_records: str,
    order_by: str = "",
    spatial_filter_geometry: dict[str, Any] | None = None,
    spatial_filter_geometry_type: str | None = None,
    spatial_rel: str = "esriSpatialRelIntersects",
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "where": where.strip() or "1=1",
        "outFields": out_fields.strip() or "*",
        "returnGeometry": "true" if return_geometry else "false",
        "resultRecordCount": max_records,
    }

    if return_geometry:
        params["outSR"] = "3857"

    if order_by.strip():
        params["orderByFields"] = order_by.strip()

    if spatial_filter_geometry and spatial_filter_geometry_type:
        import json

        params.update(
            {
                "geometry": json.dumps(spatial_filter_geometry, ensure_ascii=False),
                "geometryType": spatial_filter_geometry_type,
                "inSR": "4326",
                "spatialRel": spatial_rel or "esriSpatialRelIntersects",
            }
        )

    return params
