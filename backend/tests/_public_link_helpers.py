"""The URL a Download click on the public page opens.

A charged public download needs a ticket (`routers/public.py` download-ticket):
mail link scanners fetch every URL a mailed page links to, so a plain GET never
pays. Tests about ranges, budgets and resumes fetch the bytes the way the page
does - with a real ticket - and `test_public_download_tickets.py` pins the
ticket itself. Signed directly rather than POSTed for, so a test can hold a URL
for a link it is about to spend (the mint refuses a spent link).
"""
from __future__ import annotations

from app.services import public_link as public_link_svc


def public_download_url(db, token: str, file_id: str | None = None) -> str:
    link = public_link_svc.get_link_by_token(db, token)
    target = file_id or public_link_svc.ZIP_TARGET
    ticket = public_link_svc.issue_download_ticket(link.id, target, 900)
    path = "download-zip" if file_id is None else f"files/{file_id}/download"
    return f"/api/public/{token}/{path}?t={ticket}"
