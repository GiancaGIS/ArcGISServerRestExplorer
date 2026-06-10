from arcgis_server_rest_explorer.app import ArcGISRestExplorer


def test_redacts_multiple_sensitive_query_params():
    url = (
        "https://example.com/arcgis/rest/services?"
        "token=abc&access_token=def&api_key=ghi&apikey=jkl&key=mno&where=1%3D1"
    )

    redacted = ArcGISRestExplorer.redact_token_from_url(url)

    assert "abc" not in redacted
    assert "def" not in redacted
    assert "ghi" not in redacted
    assert "jkl" not in redacted
    assert "mno" not in redacted
    assert "where=1%3D1" in redacted
