"""Single import surface for ORM models.

Importing `from app.models import *` in `alembic/env.py` registers every model
on `Base.metadata` so autogenerate (and `Base.metadata.create_all` in tests)
sees the full schema.
"""
from .api_token import ApiToken
from .app_setting import AppSetting
from .audit_log import AuditEventType, AuditLog
from .client_employee_connection import ClientEmployeeConnection, ConnectionSource
from .download_log import DownloadLog, DownloadVia
from .email_verify_token import EmailVerifyToken
from .file import File, FileState
from .group import Group
from .group_member import GroupMember
from .invite_token import InviteToken
from .known_device import KnownDevice
from .login_attempt import LoginAttempt, LoginOutcome
from .notification import Notification, NotificationCategory
from .oidc_provider import OIDCPreset, OIDCProvider
from .password_reset_token import PasswordResetToken
from .public_link import PublicLink
from .public_link_attempt import PublicLinkAttempt, PublicLinkAttemptOutcome
from .refresh_token import RefreshToken
from .share import Share, ShareKind, ShareState
from .share_recipient import ShareRecipient
from .user import Locale, User, UserRole
from .user_notification_preference import (
    NotificationChannel,
    UserNotificationPreference,
)
from .user_recovery_code import UserRecoveryCode
from .user_totp import UserTOTP
from .user_webauthn_credential import UserWebAuthnCredential

__all__ = [
    "ApiToken",
    "AppSetting",
    "AuditEventType",
    "AuditLog",
    "ClientEmployeeConnection",
    "ConnectionSource",
    "DownloadLog",
    "DownloadVia",
    "EmailVerifyToken",
    "File",
    "FileState",
    "Group",
    "GroupMember",
    "InviteToken",
    "KnownDevice",
    "Locale",
    "LoginAttempt",
    "LoginOutcome",
    "Notification",
    "NotificationCategory",
    "NotificationChannel",
    "OIDCPreset",
    "OIDCProvider",
    "PasswordResetToken",
    "PublicLink",
    "PublicLinkAttempt",
    "PublicLinkAttemptOutcome",
    "RefreshToken",
    "Share",
    "ShareKind",
    "ShareRecipient",
    "ShareState",
    "User",
    "UserNotificationPreference",
    "UserRecoveryCode",
    "UserRole",
    "UserTOTP",
    "UserWebAuthnCredential",
]
