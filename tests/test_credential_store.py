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
