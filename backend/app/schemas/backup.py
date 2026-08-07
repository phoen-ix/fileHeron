"""Schemas for the configuration backup / restore admin endpoints."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Category = Literal["settings_branding", "oidc_webhooks", "groups", "users", "logs"]
SecretMode = Literal["passphrase", "ciphertext", "exclude"]

_MIN_PASSPHRASE = 12


class BackupExportRequest(BaseModel):
    categories: list[Category] = Field(min_length=1)
    secret_mode: SecretMode
    passphrase: str | None = None
    include_env: bool = False
    # The caller's OWN password, re-confirmed. Export is the one admin surface
    # that reads secrets back out (password hashes, decrypted TOTP seeds, and
    # with include_env the JWT/DB secrets), so it gets the same re-auth gate the
    # self-update routes have always had. Distinct from `passphrase`, which
    # encrypts the artifact.
    password: str = Field(..., min_length=1, max_length=512)

    @model_validator(mode="after")
    def _check(self) -> BackupExportRequest:
        if self.secret_mode == "passphrase":
            if not self.passphrase or len(self.passphrase) < _MIN_PASSPHRASE:
                raise ValueError(
                    f"A passphrase of at least {_MIN_PASSPHRASE} characters is required "
                    "for passphrase mode."
                )
        elif self.passphrase:
            raise ValueError("passphrase is only valid with secret_mode=passphrase.")
        if self.include_env and self.secret_mode != "passphrase":
            raise ValueError(
                "include_env requires secret_mode=passphrase (the snapshot contains "
                "plaintext infrastructure secrets)."
            )
        # de-dupe while preserving order
        self.categories = list(dict.fromkeys(self.categories))
        return self


class BackupImportSummaryResponse(BaseModel):
    dry_run: bool
    secret_mode: str
    categories: list[str]
    shares_to_invalidate: int
    files_deleted: int = 0
    counts: dict[str, Any]
    purged_users: list[str]
    purged_groups: list[str]
    sessions_revoked: int
    env_snapshot_present: bool
    env_dotenv: str | None = None
    version_warning: str | None = None
    warnings: list[str]
    # What the import INSTALLS. A count told an admin nothing about what they
    # were approving (audit #2).
    admins_installed: list[str] = []
    oidc_issuers: list[str] = []
    webhook_urls: list[str] = []
