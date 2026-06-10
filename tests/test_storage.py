from arcgis_server_rest_explorer.storage import atomic_write_json, load_json_file


def test_atomic_write_and_load_json(tmp_path):
    path = tmp_path / "data.json"
    atomic_write_json(path, [{"name": "demo"}])

    data, error = load_json_file(path, [])

    assert error is None
    assert data == [{"name": "demo"}]


def test_invalid_json_returns_default_and_error(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")

    data, error = load_json_file(path, [])

    assert data == []
    assert error
