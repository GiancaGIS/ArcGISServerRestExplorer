from arcgis_server_rest_explorer.app import ArcGISRestExplorer
from arcgis_server_rest_explorer.operations import normalize_gp_input_params
from arcgis_server_rest_explorer.workers import GpJobWorker


def app_shell():
    return ArcGISRestExplorer.__new__(ArcGISRestExplorer)


def test_gp_async_metadata_exposes_submit_job_with_input_params():
    app = app_shell()
    metadata = {
        "executionType": "esriExecutionTypeAsynchronous",
        "parameters": [
            {"name": "Input_Features", "direction": "esriGPParameterDirectionInput"},
            {"name": "Output", "direction": "esriGPParameterDirectionOutput"},
        ],
    }

    operations = app.gp_operation_definitions(metadata)

    assert operations == [
        {
            "label": "Submit Job",
            "endpoint": "submitJob",
            "mode": "gp_submit",
            "params": {"Input_Features": ""},
        }
    ]


def test_gp_sync_metadata_exposes_execute():
    app = app_shell()
    metadata = {"executionType": "esriExecutionTypeSynchronous", "parameters": []}

    operations = app.gp_operation_definitions(metadata)

    assert operations[0]["label"] == "Execute"
    assert operations[0]["endpoint"] == "execute"
    assert operations[0]["mode"] == "request"


def test_layer_operations_include_attachment_and_relationship_helpers():
    app = app_shell()
    metadata = {
        "hasAttachments": True,
        "relationships": [{"id": 2}],
    }

    labels = [op["label"] for op in app.layer_operation_definitions("https://example.com/FeatureServer/0", metadata)]

    assert "Query features" in labels
    assert "Query attachments" in labels
    assert "Query related records" in labels


def test_gp_job_worker_keeps_token_for_status_polling():
    assert GpJobWorker.token_params({"token": "abc", "f": "json"}) == {"token": "abc"}
    assert GpJobWorker.token_params({"f": "json"}) == {}


def test_gp_string_input_serializes_structured_value_as_valid_string():
    metadata = {
        "parameters": [
            {
                "name": "Payload",
                "dataType": "GPString",
                "direction": "esriGPParameterDirectionInput",
            }
        ]
    }

    params = normalize_gp_input_params(metadata, {"Payload": {"name": "Roma", "ids": [1, 2]}})

    assert params["Payload"] == '{"name":"Roma","ids":[1,2]}'


def test_gp_string_input_keeps_existing_string_value():
    metadata = {
        "parameters": [
            {
                "name": "Payload",
                "dataType": "GPString",
                "direction": "esriGPParameterDirectionInput",
            }
        ]
    }

    params = normalize_gp_input_params(metadata, {"Payload": '{"name":"Roma"}'})

    assert params["Payload"] == '{"name":"Roma"}'


def test_gp_input_validation_rejects_invalid_boolean():
    metadata = {
        "parameters": [
            {
                "name": "Enabled",
                "dataType": "GPBoolean",
                "direction": "esriGPParameterDirectionInput",
            }
        ]
    }

    try:
        normalize_gp_input_params(metadata, {"Enabled": "maybe"})
    except ValueError as exc:
        assert "Enabled" in str(exc)
    else:
        raise AssertionError("Expected invalid GPBoolean input to raise ValueError")
