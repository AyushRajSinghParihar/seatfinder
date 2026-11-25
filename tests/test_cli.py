import json

from typer.testing import CliRunner

from seatfinder.cli import app

runner = CliRunner()


def test_cities_json_envelope():
    result = runner.invoke(app, ["cities", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1"
    assert payload["command"] == "cities"
    assert payload["ok"] is True
    assert any(city["name"] == "Bengaluru" for city in payload["data"]["cities"])


def test_schema_command_is_machine_readable():
    result = runner.invoke(app, ["schema", "--compact"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "envelope" in payload
    assert "seats" in payload["commands"]
    assert payload["exit_codes"]["0"] == "success"


def test_global_json_flag_applies_to_subcommands():
    result = runner.invoke(app, ["--json", "cities", "--compact"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["command"] == "cities"


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout
