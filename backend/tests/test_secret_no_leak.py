"""A secret's content leaves the server in exactly one place: the reveal
response (v2.24.0).

This walks a secret through everything that writes something down - creation
to a user, an address and a link, a wrong passphrase, views through all three,
the sender's notices, the burn - and then looks for a canary content string,
the passphrase and the link tokens EVERYWHERE: every row of every table, every
log record, every queued mail, every webhook payload, every error body. Generic
on purpose: a new table, log line or payload that picks one up fails here
without anyone having to remember this feature exists.
"""
from __future__ import annotations

import json
import logging

import pytest
from sqlalchemy import select

from app.database import Base
from app.models.secret import Secret, SecretState
from app.models.user import UserRole
from app.services import job_queue, secret_reveal
from app.services import webhook as webhook_svc

from ._secret_helpers import PW, enable, h, send, token_of

CANARY = "CANARY-7f3a91-content-must-not-leak"
PASSPHRASE = "CANARY-passphrase-must-not-leak"


def _all_values(db) -> list[str]:
    out: list[str] = []
    for table in Base.metadata.tables.values():
        for row in db.execute(select(table)).all():
            out.extend(str(v) for v in row if v is not None)
    return out


@pytest.mark.asyncio
async def test_nothing_but_the_reveal_ever_carries_the_secret(
    db, make_user, client, login_as, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)
    jobs: list[dict] = []
    monkeypatch.setattr(job_queue, "enqueue", lambda name, *_a, **kw: jobs.append({"name": name, **kw}))
    hooks: list[dict] = []
    monkeypatch.setattr(
        webhook_svc, "emit_after_commit", lambda _db, event, payload: hooks.append({"event": event, **payload})
    )

    enable(db)
    sender = make_user(email="s@test.local", role=UserRole.employee, password=PW)
    reader = make_user(email="r@test.local", role=UserRole.employee, password=PW)
    created = send(
        db,
        sender,
        users=(reader,),
        emails=("outside@example.com",),
        link=True,
        content=CANARY,
        passphrase=PASSPHRASE,
        max_views=1,
        notify=True,
    )
    sid = created.secret.id
    link_token = created.link_token
    mail = next(j for j in jobs if j.get("to") == "outside@example.com")
    email_token = token_of(next(w for w in mail["text_body"].split() if "/s#" in w))

    token, _ = await login_as(reader.email, PW)
    responses = []
    wrong = await client.post(
        f"/api/secrets/{sid}/reveal", json={"passphrase": "not it"}, headers=h(token)
    )
    responses.append(wrong)
    assert wrong.status_code == 403
    right = await client.post(
        f"/api/secrets/{sid}/reveal", json={"passphrase": PASSPHRASE}, headers=h(token)
    )
    assert right.status_code == 200 and right.json()["content"] == CANARY
    via_link = await client.post(
        "/api/public/secrets/reveal", json={"token": link_token, "passphrase": PASSPHRASE}
    )
    assert via_link.status_code == 200 and via_link.json()["content"] == CANARY
    result = secret_reveal.reveal_by_token(
        db, token=email_token, passphrase=PASSPHRASE, ip="198.51.100.4"
    )
    assert result.content == CANARY and result.ended is True
    responses.append(await client.post("/api/public/secrets/peek", json={"token": link_token}))
    sender_token, _ = await login_as(sender.email, PW)
    responses.append(await client.get(f"/api/secrets/{sid}", headers=h(sender_token)))
    responses.append(await client.get("/api/secrets?box=sent", headers=h(sender_token)))
    responses.append(await client.get("/api/secrets?box=received", headers=h(token)))

    db.expire_all()
    s = db.get(Secret, sid)
    assert s.state == SecretState.burned and s.ciphertext is None and s.key_encrypted is None

    needles = {"content": CANARY, "passphrase": PASSPHRASE, "link": link_token, "mail link": email_token}
    haystacks = {
        "database": _all_values(db),
        "log records": [
            json.dumps({k: str(v) for k, v in r.__dict__.items()}) for r in caplog.records
        ],
        "webhook payloads": [json.dumps(p, default=str) for p in hooks],
        "other responses": [r.text for r in responses],
        # The outside address's mail must carry ITS link - nothing else.
        "queued mails": [
            json.dumps(j, default=str) for j in jobs if j.get("to") != "outside@example.com"
        ],
    }
    for where, values in haystacks.items():
        for what, needle in needles.items():
            hits = [v[:120] for v in values if needle in v]
            assert not hits, f"the {what} leaked into {where}: {hits}"
    # Vacuity guards: each capture saw something, and the search does find the
    # canary where it belongs - in the reveal response.
    assert hooks, "the webhook capture saw nothing - this test would pass vacuously"
    assert caplog.records, "the log capture saw nothing"
    assert any("secret" in v for v in haystacks["database"])
    assert CANARY in right.text and CANARY in via_link.text
    assert any(CANARY not in m["text_body"] for m in jobs if m.get("to") == "outside@example.com")
    assert PASSPHRASE not in mail["text_body"] and PASSPHRASE not in (mail["html_body"] or "")
    assert CANARY not in mail["text_body"] and CANARY not in (mail["html_body"] or "")
