"""Frontend and cross-surface findings from audit #2.

Structural, because these are single-file behaviours whose failure mode was a
value or a wiring that looked plausible in the source - and because this repo
already keeps its cross-language checks here (test_error_log_path_redaction.py,
test_admin_log_date_filters.py).
"""
from __future__ import annotations

import pathlib

import pytest

FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"


def _read(rel: str) -> str:
    return (FRONTEND / rel).read_text()


def _code(text: str) -> str:
    """Drop `//` line comments. These assertions are about what the code does;
    matching on prose is the exact defect zip-5 was filed for."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("//")
    )


def test_an_unscanned_file_is_not_labelled_ready():
    """`ready_unscanned` had a green "Ready" pill and an enabled Download button
    that the backend refuses 425 unconditionally, so the toast contradicted the
    row's own label - and the bulk ZIP quietly handed over an archive missing
    those files."""
    src = _read("components/FileRow.vue")
    can = _code(src.split("function canDownload")[1].split("}")[0])
    assert "ready_unscanned" not in can, "the download button is offered for a 425"
    pill = _code(src.split("function pillForFile")[1].split("\n}")[0])
    assert "'warn'" in pill and "ready_unscanned" in pill

    import json

    for loc in ("en", "de"):
        messages = json.loads(
            (FRONTEND / "i18n" / "locales" / f"{loc}.json").read_text()
        )
        label = messages["files"]["state"]["ready_unscanned"].lower()
        assert "ready" not in label and "bereit" not in label, (
            f"{loc}: an unscanned file is still labelled ready"
        )


def test_a_group_header_selects_only_its_own_group():
    """The header checkbox called `selectAllActive`, so ticking "the three
    shares to this contractor" selected all forty active shares on the page -
    and the action behind the bulk bar unlinks bytes from disk."""
    src = _read("views/ShareList.vue")
    header = src.split('class="select-col"')[1][:600]
    assert "setGroupSelection" in header
    assert "selectAllActive" not in header


def test_the_upload_threshold_follows_the_server():
    """A build-time constant against an admin-tunable server cap: lowering
    `uploads.max_direct_bytes` made every file between the new cap and 100 MB
    stream in full and then fail, repeatably, while a much bigger file worked."""
    src = _read("composables/useUpload.ts")
    assert "site.maxDirectUploadBytes" in src
    assert "max_direct_upload_bytes" in _read("stores/site.ts")


def test_the_sse_stream_keeps_retrying():
    """Five failures at the backoff is ~22 seconds - shorter than a routine
    in-app Update - and the composable then stopped forever, so every open tab
    lost live notifications with no error and no retry control."""
    src = _read("composables/useSSE.ts")
    assert "RETRY_AFTER_GIVEUP_MS" in src
    giveup = src.split("MAX_CONSECUTIVE_ERRORS) {")[1].split("_scheduleReconnect()")[0]
    assert "stopped = true" not in giveup, "the composable still gives up permanently"


def test_the_scheduled_tasks_page_starts_its_stream():
    """The stream was never started, so a hand-triggered cron showed `running`
    forever and an operator concluded the worker was wedged."""
    src = _read("views/AdminScheduledTasks.vue")
    assert "sse.start()" in src
    assert "Object.assign(it, before)" in src, (
        "a refused save left the row showing a cadence that is not in effect"
    )


def test_the_quarantine_dialog_closes_on_escape():
    """Escape did nothing on the one dialog in the admin shell that irreversibly
    destroys evidence of an infected upload: the backdrop's @keydown.escape was
    never on the key event's propagation path."""
    src = _read("views/AdminQuarantine.vue")
    assert "useEscapeToClose(" in src
    # A HANDLER binding, not the word in a comment explaining why it is gone.
    assert '@keydown.escape="' not in src


def test_the_admin_user_page_reports_a_failed_load():
    """It painted the sidebar and an entirely empty content area - no message,
    no not-found state. A bookmarked link to a purged account read as a broken
    admin panel."""
    src = _read("views/AdminUserDetail.vue")
    assert "loadError" in src
    assert 'v-else-if="loadError"' in src


def test_a_blank_quota_field_means_unlimited():
    """`v-model.number` on an empty field yields '' (not null), which 422s and
    surfaces as "Something didn't work" - so the "leave blank for unlimited" the
    help text promises was impossible."""
    src = _read("views/AdminUserDetail.vue")
    assert "editQuota.value as unknown as string) === ''" in src


def test_both_email_change_links_are_shown():
    """In verify_both mode only the new-address link was rendered, so on an
    SMTP-less instance the change could never complete: the old address is never
    confirmed, and after 24h the token expires."""
    src = _read("views/AdminUserDetail.vue")
    assert "changeEmailOldLink" in src
    assert "old_confirm_url" in src


def test_the_imap_test_button_sends_the_edited_form():
    """It tested the SAVED settings while the admin looked at an edited form: a
    typo'd new host reported "Connection OK" and was saved, and a correct new
    host reported "Connection failed" because the stored one was broken."""
    src = _read("views/AdminSettingsImap.vue")
    call = src.split("await testImap(")[1][:400]
    assert "form.value.host" in call


def test_the_mail_log_deep_link_filter_is_clearable():
    """It was invisible and unclearable: an admin who then typed an address into
    the visible Recipient box got zero rows for a query that still carried
    `recipient_user_id`, and concluded the mail had never been sent."""
    src = _read("views/AdminMailLog.vue")
    assert "recipientUserId = null" in src


def test_the_public_share_page_refreshes_after_a_download():
    """A plain `<a href>` cannot report a failure. A link with download_limit=1
    over two files kept saying "1 download left" and kept offering the second
    file after the first click spent the budget."""
    src = _read("views/PublicShare.vue")
    assert "refreshAfterDownload" in src
    assert src.count("@click=\"refreshAfterDownload\"") >= 2


def test_a_password_change_keeps_this_device_signed_in():
    """`change_password` revokes every refresh token, this device's included, so
    the tab kept working on its unexpired access token and was bounced to /login
    up to 15 minutes later, mid-task."""
    backend = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app" / "routers" / "account.py"
    ).read_text()
    body = backend.split("async def change_password(")[1].split("\n@router")[0]
    assert "finalize_successful_login" in body
    assert "fh_refresh" in body
    spa = _read("views/Account.vue")
    assert "setAccessToken(res.data.access_token)" in spa


@pytest.mark.parametrize(
    "needle",
    ["admins_installed", "oidc_issuers", "webhook_urls"],
)
def test_the_import_preview_names_what_it_installs(needle):
    """"users_insert: 6" told an admin nothing about what they were approving: a
    backup handed over "from the old server" can carry an admin row with a known
    password hash and a webhook shipping every share event to an external
    host."""
    assert needle in _read("views/AdminSettingsBackup.vue")
