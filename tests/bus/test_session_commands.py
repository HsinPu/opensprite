from opensprite.bus.session_commands import session_command_catalog


def test_session_command_catalog_is_derived_from_registry():
    catalog = session_command_catalog()
    commands = {item["name"]: item for item in catalog["commands"]}

    assert commands["help"]["usage"] == "/help [command]"
    assert commands["curator"]["usage"] == "/curator <status|run [scope]|pause|resume|help>"
    assert commands["curator"]["subcommands"] == ["status", "run", "pause", "resume", "help"]
    assert commands["cron"]["category"] == "Automation"
    assert {category["name"] for category in catalog["categories"]} >= {"Info", "Session", "Automation", "Work", "Maintenance"}
