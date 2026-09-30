import pytest

from voice_creator_mcp.__main__ import main, parse_args


def test_stdio_is_the_default_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICE_CREATOR_TRANSPORT", raising=False)
    assert parse_args([]).transport == "stdio"


def test_transport_can_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_CREATOR_TRANSPORT", "http")
    assert parse_args([]).transport == "http"


def test_http_without_oauth_exits_with_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("VOICE_CREATOR_OAUTH_ISSUER", "VOICE_CREATOR_RESOURCE_URL", "VOICE_CREATOR_AUTH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOICE_CREATOR_DATA_DIR", str(tmp_path))

    with pytest.raises(SystemExit) as exit_info:
        main(["--transport", "http", "--host", "0.0.0.0"])

    assert exit_info.value.code == 2
    assert "requires OAuth" in capsys.readouterr().err
