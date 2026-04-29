from opensprite.config.mcp_settings import MCPSettingsService
from opensprite.config.schema import Config


def test_mcp_settings_defaults_remote_servers_to_streamable_http(tmp_path):
    config_path = tmp_path / "opensprite.json"
    Config.copy_template(config_path)

    service = MCPSettingsService(config_path)
    payload = service.upsert_server(
        "remote",
        {
            "url": "https://example.test/mcp",
        },
    )

    assert payload["server"]["type"] == "streamableHttp"
    loaded = Config.load(config_path)
    assert loaded.tools.mcp_servers["remote"].type == "streamableHttp"
