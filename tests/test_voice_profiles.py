import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from voice_creator_mcp.errors import ProfileError
from voice_creator_mcp.voices.profiles import ProfileStore

FIXED_NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> ProfileStore:
    return ProfileStore(tmp_path, clock=lambda: FIXED_NOW)


def create(store: ProfileStore, name: str = "mario", **kwargs):
    return store.create(
        name,
        display_name=kwargs.pop("display_name", "Mario"),
        sample_wav=b"RIFF-sample",
        consent_wav=b"RIFF-consent",
        **kwargs,
    )


def test_create_and_get_round_trip(store: ProfileStore) -> None:
    created = create(store, language_code="es-MX")

    loaded = store.get("mario")

    assert loaded == created
    assert loaded.display_name == "Mario"
    assert loaded.language_code == "es-MX"
    assert loaded.created_at == FIXED_NOW.isoformat()
    assert store.read_sample("mario") == b"RIFF-sample"
    assert store.read_consent("mario") == b"RIFF-consent"


@pytest.mark.parametrize("name", ["", "../escape", "Mario", "has space", "a" * 65, "-leading"])
def test_rejects_unsafe_profile_names(store: ProfileStore, name: str) -> None:
    with pytest.raises(ProfileError, match="profile name"):
        create(store, name)


def test_refuses_to_overwrite_unless_asked(store: ProfileStore) -> None:
    create(store)
    with pytest.raises(ProfileError, match="already exists"):
        create(store)

    replaced = create(store, display_name="Mario v2", overwrite=True)
    assert replaced.display_name == "Mario v2"


def test_missing_profile_is_reported(store: ProfileStore) -> None:
    with pytest.raises(ProfileError, match="not found"):
        store.get("ghost")


def test_list_is_sorted_by_name(store: ProfileStore) -> None:
    create(store, "zoe")
    create(store, "ana")
    assert [profile.name for profile in store.list()] == ["ana", "zoe"]


def test_update_persists_allowed_fields(store: ProfileStore) -> None:
    create(store)

    store.update("mario", consent_signature="sig-1", voice_id="voice_abc")

    reloaded = store.get("mario")
    assert reloaded.consent_signature == "sig-1"
    assert reloaded.voice_id == "voice_abc"
    assert reloaded.synthesis_voice == "voice_abc"


def test_update_rejects_unknown_fields(store: ProfileStore) -> None:
    create(store)
    with pytest.raises(ProfileError, match="sample_path"):
        store.update("mario", sample_path="/etc/passwd")


def test_voice_key_is_used_when_no_voice_id(store: ProfileStore) -> None:
    create(store)
    store.update("mario", voice_key="voicekey_xyz", voice_expire_time="2026-10-07T00:00:00Z")
    assert store.get("mario").synthesis_voice == "voicekey_xyz"


def test_delete_removes_all_files(store: ProfileStore, tmp_path: Path) -> None:
    create(store)
    store.delete("mario")

    assert store.list() == []
    assert not (tmp_path / "profiles" / "mario").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
def test_biometric_files_are_private(store: ProfileStore, tmp_path: Path) -> None:
    create(store)
    profile_dir = tmp_path / "profiles" / "mario"

    assert stat.S_IMODE(profile_dir.stat().st_mode) == 0o700
    for child in profile_dir.iterdir():
        assert stat.S_IMODE(child.stat().st_mode) == 0o600


def test_public_dict_masks_secrets(store: ProfileStore) -> None:
    create(store)
    store.update("mario", consent_signature="sig-secret", voice_key="voicekey_secret123")

    public = store.get("mario").to_public_dict()

    assert "sig-secret" not in str(public)
    assert "voicekey_secret123" not in str(public)
    assert public["has_consent_signature"] is True
    assert public["voice_key"].startswith("voicekey_")
    assert public["voice_key"].endswith("…")
