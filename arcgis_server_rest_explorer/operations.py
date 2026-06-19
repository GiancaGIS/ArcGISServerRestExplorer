import json
from typing import Any


def layer_operation_definitions(url: str, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    operations = [
        {
            "label": "Query features",
            "endpoint": "query",
            "mode": "request",
            "params": {"where": "1=1", "outFields": "*", "returnGeometry": "false", "resultRecordCount": "10"},
        },
        {
            "label": "Query count",
            "endpoint": "query",
            "mode": "request",
            "params": {"where": "1=1", "returnCountOnly": "true", "returnGeometry": "false"},
        },
        {
            "label": "Query object IDs",
            "endpoint": "query",
            "mode": "request",
            "params": {"where": "1=1", "returnIdsOnly": "true", "returnGeometry": "false"},
        },
        {
            "label": "Validate SQL",
            "endpoint": "validateSQL",
            "mode": "request",
            "params": {"sql": "1=1"},
        },
    ]
    if metadata.get("hasAttachments"):
        operations.append({"label": "Query attachments", "endpoint": "queryAttachments", "mode": "request", "params": {"objectIds": ""}})
    relationships = metadata.get("relationships", [])
    if isinstance(relationships, list) and relationships:
        relationship_id = relationships[0].get("id", "") if isinstance(relationships[0], dict) else ""
        operations.append(
            {
                "label": "Query related records",
                "endpoint": "queryRelatedRecords",
                "mode": "request",
                "params": {"objectIds": "", "relationshipId": relationship_id, "outFields": "*"},
            }
        )
    return operations


def gp_operation_definitions(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    execution_type = metadata.get("executionType", "")
    params = default_gp_input_params(metadata)
    if execution_type == "esriExecutionTypeSynchronous":
        return [{"label": "Execute", "endpoint": "execute", "mode": "request", "params": params}]
    if execution_type == "esriExecutionTypeAsynchronous":
        return [{"label": "Submit Job", "endpoint": "submitJob", "mode": "gp_submit", "params": params}]
    return [
        {"label": "Execute", "endpoint": "execute", "mode": "request", "params": params},
        {"label": "Submit Job", "endpoint": "submitJob", "mode": "gp_submit", "params": params},
    ]


def default_gp_input_params(metadata: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    parameters = metadata.get("parameters", [])
    if not isinstance(parameters, list):
        return out
    for param in parameters:
        if not isinstance(param, dict):
            continue
        direction = param.get("direction", "esriGPParameterDirectionInput")
        name = param.get("name")
        if name and direction == "esriGPParameterDirectionInput":
            out[str(name)] = ""
    return out


def normalize_gp_input_params(metadata: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(params)
    for name, spec in gp_input_parameter_specs(metadata).items():
        if name not in normalized:
            continue
        normalized[name] = normalize_gp_input_value(name, normalized[name], str(spec.get("dataType", "")))
    return normalized


def gp_input_parameter_specs(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    specs: dict[str, dict[str, Any]] = {}
    parameters = metadata.get("parameters", [])
    if not isinstance(parameters, list):
        return specs
    for param in parameters:
        if not isinstance(param, dict):
            continue
        direction = param.get("direction", "esriGPParameterDirectionInput")
        name = param.get("name")
        if name and direction == "esriGPParameterDirectionInput":
            specs[str(name)] = param
    return specs


def normalize_gp_input_value(name: str, value: Any, data_type: str) -> Any:
    if data_type == "GPString":
        return normalize_gp_string_value(value)
    if data_type in {"GPLong", "GPDouble"}:
        return normalize_gp_number_value(name, value, data_type)
    if data_type == "GPBoolean":
        return normalize_gp_boolean_value(name, value)
    return value


def normalize_gp_string_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def normalize_gp_number_value(name: str, value: Any, data_type: str) -> int | float | str:
    if value == "":
        return value
    try:
        if data_type == "GPLong":
            return int(value)
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Parameter '{name}' must be numeric for {data_type}.") from exc


def normalize_gp_boolean_value(name: str, value: Any) -> bool | str:
    if isinstance(value, bool):
        return value
    if value == "":
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    raise ValueError(f"Parameter '{name}' must be a boolean value.")


def build_gp_task_summary(data: dict[str, Any]) -> dict[str, Any]:
    parameters = data.get("parameters", [])
    return {
        "name": data.get("name"),
        "executionType": data.get("executionType"),
        "parameters": len(parameters) if isinstance(parameters, list) else 0,
        "resultMapServerName": data.get("resultMapServerName"),
    }
