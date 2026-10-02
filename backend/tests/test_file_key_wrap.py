"""utils/crypto.wrap_file_key / unwrap_file_key - a file's data key, wrapped by
the instance key with its row bound inside."""
from __future__ import annotations

import pytest

from app.utils import crypto


def test_round_trip():
    dek = bytes(range(32))
    wrapped = crypto.wrap_file_key(dek, kind="file", row_id="f-1")
    assert len(wrapped) <= 255, "must fit files.key_encrypted"
    assert crypto.unwrap_file_key(wrapped, kind="file", row_id="f-1") == dek


@pytest.mark.parametrize(("kind", "row_id"), [("file", "f-2"), ("inbound_attachment", "f-1")])
def test_a_key_moved_to_another_row_or_kind_is_refused(kind, row_id):
    wrapped = crypto.wrap_file_key(bytes(32), kind="file", row_id="f-1")
    with pytest.raises(crypto.FileKeyMismatchError):
        crypto.unwrap_file_key(wrapped, kind=kind, row_id=row_id)


def test_another_instance_key_is_undecryptable_not_a_mismatch(monkeypatch):
    wrapped = crypto.wrap_file_key(bytes(32), kind="file", row_id="f-1")
    monkeypatch.setattr(crypto.settings, "JWT_SECRET", "another_secret_at_least_thirty_two_characters!!")
    monkeypatch.setattr(crypto, "_fernet_instance", None)
    with pytest.raises(crypto.SecretUndecryptableError) as exc:
        crypto.unwrap_file_key(wrapped, kind="file", row_id="f-1")
    assert not isinstance(exc.value, crypto.FileKeyMismatchError)
