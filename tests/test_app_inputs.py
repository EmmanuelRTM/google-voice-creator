import base64
from pathlib import Path

import pytest

from voice_creator.errors import AudioFormatError, PathNotAllowedError
from voice_creator_mcp.app.inputs import PathPolicy, load_audio_input


def test_unrestricted_policy_resolves_any_path(tmp_path: Path) -> None:
    assert PathPolicy(None).resolve(str(tmp_path / "a.wav")) == tmp_path / "a.wav"


def test_confined_policy_accepts_paths_inside_roots(tmp_path: Path) -> None:
    policy = PathPolicy([tmp_path])
    assert policy.resolve(str(tmp_path / "voices" / "a.wav")) == tmp_path / "voices" / "a.wav"


@pytest.mark.parametrize("raw", ["/etc/passwd", "{root}/../escape.wav"])
def test_confined_policy_rejects_outside_paths(tmp_path: Path, raw: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(PathNotAllowedError, match="outside the allowed directories"):
        PathPolicy([root]).resolve(raw.format(root=root))


def test_symlinks_cannot_escape_the_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "link.wav").symlink_to(tmp_path / "secret.wav")
    with pytest.raises(PathNotAllowedError):
        PathPolicy([root]).resolve(str(root / "link.wav"))


def test_disabled_policy_points_to_base64(tmp_path: Path) -> None:
    with pytest.raises(PathNotAllowedError, match="base64"):
        PathPolicy(()).resolve(str(tmp_path / "a.wav"))


def test_load_from_base64() -> None:
    data = load_audio_input(
        path=None,
        base64_data=base64.b64encode(b"RIFF").decode(),
        policy=PathPolicy(()),
        label="sample",
    )
    assert data == b"RIFF"


def test_load_from_path(tmp_path: Path) -> None:
    (tmp_path / "a.wav").write_bytes(b"RIFF")
    data = load_audio_input(
        path=str(tmp_path / "a.wav"), base64_data=None, policy=PathPolicy(None), label="sample"
    )
    assert data == b"RIFF"


@pytest.mark.parametrize(("path", "b64"), [(None, None), ("/tmp/a.wav", "UklGRg==")])
def test_exactly_one_source_is_required(path, b64) -> None:
    with pytest.raises(ValueError, match="sample_path or sample_base64"):
        load_audio_input(path=path, base64_data=b64, policy=PathPolicy(None), label="sample")


def test_invalid_base64_is_rejected() -> None:
    with pytest.raises(ValueError, match="base64"):
        load_audio_input(
            path=None, base64_data="not base64!!", policy=PathPolicy(None), label="sample"
        )


def test_oversized_inputs_are_rejected_before_reading(tmp_path: Path) -> None:
    big = tmp_path / "big.wav"
    big.write_bytes(b"\0" * 2048)
    with pytest.raises(AudioFormatError, match="too large"):
        load_audio_input(
            path=str(big), base64_data=None, policy=PathPolicy(None), label="sample", max_bytes=1024
        )
    with pytest.raises(AudioFormatError, match="too large"):
        load_audio_input(
            path=None,
            base64_data=base64.b64encode(b"\0" * 2048).decode(),
            policy=PathPolicy(None),
            label="sample",
            max_bytes=1024,
        )


def test_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(AudioFormatError, match="not found"):
        load_audio_input(
            path=str(tmp_path / "missing.wav"),
            base64_data=None,
            policy=PathPolicy(None),
            label="sample",
        )
