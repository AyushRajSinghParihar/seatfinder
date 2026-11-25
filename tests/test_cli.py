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
