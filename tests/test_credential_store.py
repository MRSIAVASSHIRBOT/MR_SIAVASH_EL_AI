import base64
import json
import os
from uuid import uuid4

import pytest

from el_ai.integrations.secrets import (
    EncryptedFileCredentialStore,
    SecretStoreError,
    build_credential_store,
)


def _key() -> str:
    return base64.b64encode(b"k" * 32).decode("ascii")


def test_encrypted_file_store_round_trips_without_persisting_plaintext(tmp_path):
    store = EncryptedFileCredentialStore(tmp_path / "credentials", _key())
    client_id = str(uuid4())
    credentials = {
        "access_token": "private-access-token",
        "refresh_token": "private-refresh-token",
        "scopes": ["tasks:read"],
    }

    store.save(client_id, credentials)
    stored_file = tmp_path / "credentials" / f"{client_id}.json"
    raw = stored_file.read_bytes()

    assert store.load(client_id) == credentials
    assert b"private-access-token" not in raw
    assert b"private-refresh-token" not in raw
    assert os.stat(stored_file).st_mode & 0o777 == 0o600
    assert os.stat(stored_file.parent).st_mode & 0o777 == 0o700


def test_encrypted_file_store_rejects_wrong_key_and_tampering(tmp_path):
    client_id = str(uuid4())
    store = EncryptedFileCredentialStore(tmp_path / "credentials", _key())
    store.save(client_id, {"access_token": "secret"})

    wrong_key_store = EncryptedFileCredentialStore(
        tmp_path / "credentials", base64.b64encode(b"x" * 32).decode("ascii")
    )
    with pytest.raises(SecretStoreError):
        wrong_key_store.load(client_id)

    path = tmp_path / "credentials" / f"{client_id}.json"
    envelope = json.loads(path.read_text(encoding="ascii"))
    envelope["ciphertext"] = base64.b64encode(b"tampered").decode("ascii")
    path.write_text(json.dumps(envelope), encoding="ascii")
    path.chmod(0o600)
    with pytest.raises(SecretStoreError):
        store.load(client_id)


def test_encrypted_file_store_delete_is_idempotent(tmp_path):
    store = EncryptedFileCredentialStore(tmp_path / "credentials", _key())
    client_id = str(uuid4())
    store.save(client_id, {"refresh_token": "secret"})

    store.delete(client_id)
    store.delete(client_id)

    assert store.load(client_id) is None


@pytest.mark.parametrize("encoded_key", [None, "", "not-base64", base64.b64encode(b"short").decode()])
def test_encrypted_file_store_fails_closed_for_invalid_key(tmp_path, encoded_key):
    with pytest.raises(SecretStoreError):
        EncryptedFileCredentialStore(tmp_path / "credentials", encoded_key)


def test_credential_store_factory_requires_explicit_encrypted_key(monkeypatch, tmp_path):
    monkeypatch.setenv("EL_AI_CREDENTIAL_STORE", "encrypted_file")
    monkeypatch.delenv("EL_AI_CREDENTIAL_ENCRYPTION_KEY", raising=False)

    with pytest.raises(SecretStoreError):
        build_credential_store(tmp_path)

    monkeypatch.setenv("EL_AI_CREDENTIAL_ENCRYPTION_KEY", _key())
    assert isinstance(
        build_credential_store(tmp_path), EncryptedFileCredentialStore
    )


def test_encrypted_file_store_allows_ciphertext_envelope_overhead(tmp_path):
    store = EncryptedFileCredentialStore(tmp_path / "credentials", _key())
    client_id = str(uuid4())
    credentials = {"access_token": "x" * 13_000}

    store.save(client_id, credentials)

    path = tmp_path / "credentials" / f"{client_id}.json"
    assert 16 * 1024 < path.stat().st_size <= store.MAX_FILE_BYTES
    assert store.load(client_id) == credentials


def test_encrypted_file_store_rejects_non_posix_hosts(monkeypatch, tmp_path):
    monkeypatch.setattr("el_ai.integrations.secrets.os.name", "nt")

    with pytest.raises(SecretStoreError, match="POSIX host"):
        EncryptedFileCredentialStore(tmp_path / "credentials", _key())


def test_encrypted_file_store_rejects_a_symlink_as_the_credential_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    directory = tmp_path / "credentials"
    directory.symlink_to(outside, target_is_directory=True)
    store = EncryptedFileCredentialStore(directory, _key())

    with pytest.raises(SecretStoreError, match="directory is unavailable or unsafe"):
        store.save(str(uuid4()), {"access_token": "secret"})

    assert list(outside.iterdir()) == []


def test_encrypted_file_store_write_stays_on_pinned_directory_if_path_is_replaced(
    monkeypatch, tmp_path
):
    directory = tmp_path / "credentials"
    outside = tmp_path / "outside"
    pinned = tmp_path / "credentials-pinned"
    outside.mkdir()
    store = EncryptedFileCredentialStore(directory, _key())
    client_id = str(uuid4())
    original_replace = os.replace
    replaced = False

    def replace_after_swapping_path(source, destination, *, src_dir_fd=None, dst_dir_fd=None):
        nonlocal replaced
        if not replaced:
            directory.rename(pinned)
            directory.symlink_to(outside, target_is_directory=True)
            replaced = True
        original_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr("el_ai.integrations.secrets.os.replace", replace_after_swapping_path)

    store.save(client_id, {"access_token": "must-remain-in-pinned-directory"})

    assert replaced is True
    assert (pinned / f"{client_id}.json").is_file()
    assert list(outside.iterdir()) == []
    with pytest.raises(SecretStoreError):
        store.load(client_id)


def test_encrypted_file_store_delete_uses_the_pinned_directory_descriptor(
    monkeypatch, tmp_path
):
    directory = tmp_path / "credentials"
    outside = tmp_path / "outside"
    pinned = tmp_path / "credentials-pinned"
    outside.mkdir()
    store = EncryptedFileCredentialStore(directory, _key())
    client_id = str(uuid4())
    store.save(client_id, {"access_token": "secret"})
    original_unlink = os.unlink
    replaced = False

    def unlink_after_swapping_path(path, *, dir_fd=None):
        nonlocal replaced
        if not replaced:
            directory.rename(pinned)
            directory.symlink_to(outside, target_is_directory=True)
            replaced = True
        original_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr("el_ai.integrations.secrets.os.unlink", unlink_after_swapping_path)

    store.delete(client_id)

    assert replaced is True
    assert not (pinned / f"{client_id}.json").exists()
    assert list(outside.iterdir()) == []


def test_encrypted_file_store_read_uses_the_pinned_directory_descriptor(
    monkeypatch, tmp_path
):
    directory = tmp_path / "credentials"
    outside = tmp_path / "outside"
    pinned = tmp_path / "credentials-pinned"
    outside.mkdir()
    store = EncryptedFileCredentialStore(directory, _key())
    client_id = str(uuid4())
    expected = {"access_token": "must-be-read-from-pinned-directory"}
    store.save(client_id, expected)
    original_open = os.open
    replaced = False

    def open_after_swapping_path(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if path == f"{client_id}.json" and dir_fd is not None and not replaced:
            directory.rename(pinned)
            directory.symlink_to(outside, target_is_directory=True)
            replaced = True
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr("el_ai.integrations.secrets.os.open", open_after_swapping_path)

    assert store.load(client_id) == expected
    assert replaced is True
    assert list(outside.iterdir()) == []



def test_encrypted_file_store_rejects_a_symlink_in_a_parent_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = tmp_path / "parent"
    parent.symlink_to(outside, target_is_directory=True)
    store = EncryptedFileCredentialStore(parent / "credentials", _key())

    with pytest.raises(SecretStoreError, match="directory is unavailable or unsafe"):
        store.save(str(uuid4()), {"access_token": "secret"})

    assert list(outside.iterdir()) == []


def test_encrypted_file_store_write_stays_on_pinned_parent_if_parent_is_replaced(
    monkeypatch, tmp_path
):
    parent = tmp_path / "parent"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    pinned_parent = tmp_path / "parent-pinned"
    store = EncryptedFileCredentialStore(parent / "credentials", _key())
    client_id = str(uuid4())
    original_open = os.open
    replaced = False

    def open_after_swapping_parent(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if path == "credentials" and dir_fd is not None and not replaced:
            parent.rename(pinned_parent)
            parent.symlink_to(outside, target_is_directory=True)
            replaced = True
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr("el_ai.integrations.secrets.os.open", open_after_swapping_parent)

    store.save(client_id, {"access_token": "must-remain-in-pinned-parent"})

    assert replaced is True
    assert (pinned_parent / "credentials" / f"{client_id}.json").is_file()
    assert list(outside.iterdir()) == []
