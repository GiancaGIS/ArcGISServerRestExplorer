from arcgis_server_rest_explorer.app import ArcGISRestExplorer


def app_shell():
    return ArcGISRestExplorer.__new__(ArcGISRestExplorer)


def test_saved_query_without_call_type_uses_legacy_layer_label():
    app = app_shell()

    label = app.collection_target_label({"name": "Old query", "layer_name": "Parcels"})

    assert label == "Parcels"


def test_saved_operation_uses_operation_and_target_label():
    app = app_shell()

    label = app.collection_target_label(
        {
            "call_type": "operation",
            "target_name": "Buffer",
            "operation": {"label": "Submit Job"},
        }
    )

    assert label == "Submit Job - Buffer"
