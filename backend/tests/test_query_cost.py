"""Five routes whose cost grew with the instance rather than the answer.

dos-11   /api/users/search loaded EVERY non-disabled user into Python, matched
         the substring there and returned at most 50. On an admin's keystroke
         that is the whole user table per character typed.
dos-13   POST /groups/{id}/members took an unbounded id list, ran one SELECT per
         id, and then O(members x peers) queries recomputing connections pair by
         pair. It also added members one at a time, so a bad id halfway through
         left the earlier ones added under a 404.
dos-14   the admin file inventory computed `total` as a COUNT over a GROUP BY
         join against download_log - every file crossed with every download row,
         grouped, then counted, to produce a number that only depends on files.
dos-16   /admin/system/stream was the one long-lived connection an admin could
         open without limit; its sibling notification stream has capped since
         audit M4.
data-3   direct upload held a pooled DB connection for the whole client body
         read - minutes on a slow link, doing no DB work - out of a 10+20 pool.

From the 2026-07-30 audit.
"""
from __future__ import annotations

import inspect

import pytest
from sqlalchemy import event

from app.models.group import Group
from app.models.user import UserRole


@pytest.fixture
def counting(db):
    """Count SQL statements issued on the session's connection."""
    stmts: list[str] = []

    def _before(conn, cursor, statement, params, context, executemany):
        stmts.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", _before)
    try:
        yield stmts
    finally:
        event.remove(engine, "before_cursor_execute", _before)


# --- dos-11 -----------------------------------------------------------------


def test_search_does_not_load_the_whole_user_table(db, make_user, counting):
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    for i in range(30):
        make_user(email=f"u{i}@test.local", role=UserRole.employee,
                  display_name=f"Person {i}")
    db.commit()

    from app.routers.users import search

    counting.clear()
    result = search(q="Person 7", me=admin, db=db)

    assert [i.display_name for i in result.items] == ["Person 7"]
    joined = " ".join(counting).lower()
    assert "like" in joined, "the filter is still being applied in Python"
    assert "limit" in joined, "the limit is still being applied in Python"


def test_the_limit_is_in_sql(db, make_user):
    """60 matches, 50 returned - and the 10 discarded ones were never
    materialised."""
    admin = make_user(email="admin@test.local", role=UserRole.admin)
    for i in range(60):
        make_user(email=f"m{i:02d}@test.local", role=UserRole.employee,
                  display_name=f"Match {i:02d}")
    db.commit()

    from app.routers.users import search

    assert len(search(q="Match", me=admin, db=db).items) == 50


def test_a_client_still_sees_only_connected_employees(db, make_user):
    """Control: pushing the scope into SQL must not widen it."""
    from app.models.client_employee_connection import (
        ClientEmployeeConnection,
        ConnectionSource,
    )
    from app.routers.users import search

    client = make_user(email="c@test.local", role=UserRole.client)
    linked = make_user(email="linked@test.local", role=UserRole.employee)
    stranger = make_user(email="stranger@test.local", role=UserRole.employee)
    db.add(
        ClientEmployeeConnection(
            client_user_id=client.id,
            employee_user_id=linked.id,
            source=ConnectionSource.invite,
        )
    )
    db.commit()

    emails = {i.email for i in search(q="", me=client, db=db).items}
    assert emails == {linked.email}
    assert stranger.email not in emails


def test_an_employee_sees_peers_plus_connected_clients_once(db, make_user):
    """Control: the union used to be built by concatenating two Python lists
    and de-duping; as one SQL predicate it must still not double-count."""
    from app.models.client_employee_connection import (
        ClientEmployeeConnection,
        ConnectionSource,
    )
    from app.routers.users import search

    me = make_user(email="me@test.local", role=UserRole.employee)
    peer = make_user(email="peer@test.local", role=UserRole.employee)
    mine = make_user(email="mine@test.local", role=UserRole.client)
    theirs = make_user(email="theirs@test.local", role=UserRole.client)
    db.add(
        ClientEmployeeConnection(
            client_user_id=mine.id,
            employee_user_id=me.id,
            source=ConnectionSource.shared_group,
        )
    )
    db.commit()

    items = search(q="", me=me, db=db).items
    emails = [i.email for i in items]
    assert len(emails) == len(set(emails)), "a user appeared twice"
    assert set(emails) == {peer.email, mine.email}
    assert theirs.email not in emails


@pytest.mark.parametrize("needle", ["100%", "a_b", "back\\slash"])
def test_like_wildcards_in_the_query_are_literals(db, make_user, needle):
    """`%` used to be a Python substring char; in SQL it means "anything", so
    an unescaped query would match every user."""
    from app.routers.users import search

    admin = make_user(email="admin@test.local", role=UserRole.admin)
    make_user(email="hit@test.local", role=UserRole.employee, display_name=needle)
    make_user(email="miss@test.local", role=UserRole.employee, display_name="nothing")
    db.commit()

    got = [i.display_name for i in search(q=needle, me=admin, db=db).items]
    assert got == [needle]


# --- dos-13 -----------------------------------------------------------------


def test_the_member_list_is_bounded():
    from app.schemas.group import AddGroupMembersRequest

    field = AddGroupMembersRequest.model_fields["user_ids"]
    caps = [m for m in field.metadata if hasattr(m, "max_length")]
    assert caps and caps[0].max_length == 1000, "the id list is still unbounded"


def _add_batch(db, make_user, counting, *, prefix: str, peers: int, batch: int) -> int:
    """Add `batch` clients to a group that already holds `peers` employees.
    Returns the number of SELECTs the add cost."""
    from app.routers.groups import add_members_endpoint
    from app.schemas.group import AddGroupMembersRequest
    from app.services import group as group_svc

    admin = make_user(email=f"{prefix}-admin@test.local", role=UserRole.admin)
    g = Group(
        name=f"G-{prefix}", name_normalized=f"g-{prefix}", description="",
        created_by_id=admin.id,
    )
    db.add(g)
    db.flush()
    for i in range(peers):
        group_svc.add_member(
            db, actor=admin, group=g,
            user=make_user(email=f"{prefix}-p{i}@test.local", role=UserRole.employee),
        )
    ids = [
        make_user(email=f"{prefix}-c{i}@test.local", role=UserRole.client).id
        for i in range(batch)
    ]
    db.commit()

    counting.clear()
    add_members_endpoint(
        group_id=g.id,
        payload=AddGroupMembersRequest(user_ids=ids),
        request=None,
        db=db,
        admin=admin,
    )
    return len([s for s in counting if s.lstrip().upper().startswith("SELECT")])


def test_the_cost_of_adding_members_no_longer_scales_with_the_group(
    db, make_user, counting
):
    """The defect: each new member was checked against each existing peer with
    its own round-trip, twice - once to see whether the connection row existed
    and once to see whether the pair still shared a group. Adding 8 people to a
    group of 30 cost hundreds of queries; adding them to a group of 2 cost a
    dozen. After the rewrite the peer count is read once per member, so the two
    numbers are the same."""
    small = _add_batch(db, make_user, counting, prefix="small", peers=2, batch=8)
    large = _add_batch(db, make_user, counting, prefix="large", peers=30, batch=8)

    assert large <= small + 2, (
        f"{small} SELECTs against 2 peers but {large} against 30 - the per-pair "
        "round-trips are still there"
    )


def test_the_user_lookup_is_one_query_for_the_whole_batch(db, make_user, counting):
    """Separately from the recompute: the route ran `SELECT ... WHERE id = ?`
    once per id in the payload."""
    from app.routers.groups import add_members_endpoint
    from app.schemas.group import AddGroupMembersRequest

    admin = make_user(email="admin@test.local", role=UserRole.admin)
    g = Group(
        name="Batch", name_normalized="batch", description="", created_by_id=admin.id
    )
    db.add(g)
    db.flush()
    ids = [
        make_user(email=f"b{i}@test.local", role=UserRole.employee).id
        for i in range(12)
    ]
    db.commit()

    counting.clear()
    add_members_endpoint(
        group_id=g.id,
        payload=AddGroupMembersRequest(user_ids=ids),
        request=None,
        db=db,
        admin=admin,
    )
    by_id = [
        s for s in counting
        if "FROM users" in s and "users.id = ?" in s.replace("\n", " ")
    ]
    assert not by_id, f"{len(by_id)} single-user lookups; the batch load is bypassed"


def test_a_bad_id_adds_nobody(db, make_user):
    """The loop used to add-then-fail, so a typo in the last id left the
    earlier members in the group behind a 404 the caller read as "nothing
    happened"."""
    from app.middleware.errors import AppError
    from app.models.group_member import GroupMember
    from app.routers.groups import add_members_endpoint
    from app.schemas.group import AddGroupMembersRequest

    admin = make_user(email="admin@test.local", role=UserRole.admin)
    g = Group(name="Partial", name_normalized="partial", description="", created_by_id=admin.id)
    db.add(g)
    db.flush()
    good = make_user(email="good@test.local", role=UserRole.employee)
    db.commit()

    with pytest.raises(AppError) as exc:
        add_members_endpoint(
            group_id=g.id,
            payload=AddGroupMembersRequest(user_ids=[good.id, 999_999]),
            request=None,
            db=db,
            admin=admin,
        )
    assert exc.value.code == "USER_NOT_FOUND"
    db.rollback()
    assert db.query(GroupMember).filter(GroupMember.group_id == g.id).count() == 0


def test_shared_group_connections_still_appear_and_disappear(db, make_user):
    """Control for the set-arithmetic rewrite: the dynamic half of the ACL is
    the whole point of the table."""
    from app.models.client_employee_connection import ClientEmployeeConnection
    from app.services import group as group_svc

    admin = make_user(email="admin@test.local", role=UserRole.admin)
    emp = make_user(email="e@test.local", role=UserRole.employee)
    cli = make_user(email="c@test.local", role=UserRole.client)
    g = Group(name="Shared", name_normalized="shared", description="", created_by_id=admin.id)
    db.add(g)
    db.flush()

    group_svc.add_member(db, actor=admin, group=g, user=emp)
    group_svc.add_member(db, actor=admin, group=g, user=cli)
    db.commit()
    assert db.query(ClientEmployeeConnection).count() == 1

    group_svc.remove_member(db, actor=admin, group=g, user=cli)
    db.commit()
    assert db.query(ClientEmployeeConnection).count() == 0


def test_two_groups_share_one_connection_row(db, make_user):
    """Leaving one of two shared groups must NOT drop the connection."""
    from app.models.client_employee_connection import ClientEmployeeConnection
    from app.services import group as group_svc

    admin = make_user(email="admin@test.local", role=UserRole.admin)
    emp = make_user(email="e@test.local", role=UserRole.employee)
    cli = make_user(email="c@test.local", role=UserRole.client)
    g1 = Group(name="One", name_normalized="one", description="", created_by_id=admin.id)
    g2 = Group(name="Two", name_normalized="two", description="", created_by_id=admin.id)
    db.add_all([g1, g2])
    db.flush()
    for g in (g1, g2):
        group_svc.add_member(db, actor=admin, group=g, user=emp)
        group_svc.add_member(db, actor=admin, group=g, user=cli)
    db.commit()
    assert db.query(ClientEmployeeConnection).count() == 1

    group_svc.remove_member(db, actor=admin, group=g1, user=cli)
    db.commit()
    assert db.query(ClientEmployeeConnection).count() == 1, (
        "the pair still shares g2"
    )


# --- dos-14 -----------------------------------------------------------------


def test_the_total_is_not_counted_through_the_download_join(db, make_user, counting):
    from app.services import file_admin

    make_user(email="admin@test.local", role=UserRole.admin)
    db.commit()

    counting.clear()
    file_admin.list_all_files(db, page=1, page_size=10)

    # The statement that produces `total` is the one whose whole select list is
    # a count. (The paged query embeds a grouped count in its subselect - that
    # one is the aggregate, and belongs there.)
    counts = [s for s in counting if s.lstrip().lower().startswith("select count(")]
    assert counts, "no count query ran at all"
    for s in counts:
        assert "group by" not in s.lower(), (
            "total is still a COUNT over a grouped join against download_log"
        )
        assert "download_log" not in s.lower(), (
            "the count still touches download_log"
        )


def test_download_stats_survive_the_rewrite(db, make_user, tmp_path):
    """Control: the aggregates moved into a pre-grouped subselect, so a file
    with no downloads must still report 0 rather than NULL."""
    from app.models.download_log import DownloadLog
    from app.models.file import File, FileState
    from app.models.share import Share, ShareKind, ShareState
    from app.services import file_admin

    owner = make_user(email="o@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    downloaded = File(
        id="00000000-0000-0000-0000-0000000000d1", share_id=sh.id,
        original_filename="a.bin", mime_type="application/octet-stream",
        size_bytes=1, storage_path=str(tmp_path / "a.bin"),
        state=FileState.clean, uploaded_by_id=owner.id,
    )
    untouched = File(
        id="00000000-0000-0000-0000-0000000000d2", share_id=sh.id,
        original_filename="b.bin", mime_type="application/octet-stream",
        size_bytes=1, storage_path=str(tmp_path / "b.bin"),
        state=FileState.clean, uploaded_by_id=owner.id,
    )
    db.add_all([downloaded, untouched])
    db.flush()
    db.add_all([
        DownloadLog(file_id=downloaded.id, share_id=sh.id, accessed_by_user_id=owner.id),
        DownloadLog(file_id=downloaded.id, share_id=sh.id, accessed_by_user_id=owner.id),
    ])
    db.commit()

    rows, total = file_admin.list_all_files(db, page=1, page_size=10)
    by_name = {r["filename"]: r for r in rows}
    assert total == 2
    assert by_name["a.bin"]["download_count"] == 2
    assert by_name["b.bin"]["download_count"] == 0
    assert by_name["b.bin"]["last_downloaded_at"] is None


def test_sorting_by_download_count_still_works(db, make_user, tmp_path):
    """The sort keys moved off the aggregate functions onto the subselect
    columns; a silent break here would reorder the admin's inventory."""
    from app.models.download_log import DownloadLog
    from app.models.file import File, FileState
    from app.models.share import Share, ShareKind, ShareState
    from app.services import file_admin

    owner = make_user(email="o@test.local", role=UserRole.employee)
    sh = Share(created_by_id=owner.id, kind=ShareKind.outbound, state=ShareState.active)
    db.add(sh)
    db.flush()
    for n, fid in ((3, "e1"), (0, "e2"), (1, "e3")):
        f = File(
            id=f"00000000-0000-0000-0000-0000000000{fid}", share_id=sh.id,
            original_filename=f"{fid}.bin", mime_type="application/octet-stream",
            size_bytes=1, storage_path=str(tmp_path / f"{fid}.bin"),
            state=FileState.clean, uploaded_by_id=owner.id,
        )
        db.add(f)
        db.flush()
        for _ in range(n):
            db.add(
                DownloadLog(
                    file_id=f.id, share_id=sh.id, accessed_by_user_id=owner.id
                )
            )
    db.commit()

    rows, _ = file_admin.list_all_files(
        db, sort="download_count", direction="desc", page=1, page_size=10
    )
    assert [r["download_count"] for r in rows] == [3, 1, 0]


# --- dos-16 -----------------------------------------------------------------


def test_the_admin_stream_has_its_own_budget():
    """Shared with the bell's counter, five open /admin/system tabs would lock
    an admin out of their own notifications."""
    from app.services import sse as sse_svc

    for _ in range(sse_svc.MAX_STREAMS_PER_USER):
        assert sse_svc.try_acquire_admin_stream(1)
    assert not sse_svc.try_acquire_admin_stream(1)
    assert sse_svc.try_acquire_user_stream(1), "the bell shares the admin budget"

    for _ in range(sse_svc.MAX_STREAMS_PER_USER):
        sse_svc.release_admin_stream(1)
    sse_svc.release_user_stream(1)
    assert sse_svc.try_acquire_admin_stream(1)
    sse_svc.release_admin_stream(1)


def test_the_route_acquires_and_releases():
    from app.routers.admin import system

    src = inspect.getsource(system.system_stream)
    assert "try_acquire_admin_stream" in src
    assert "TOO_MANY_STREAMS" in src
    assert "release_admin_stream" in src
    assert "finally:" in src, "the slot leaks on disconnect"


# --- data-3 -----------------------------------------------------------------


def test_the_pooled_connection_is_released_before_the_body_read():
    from app.routers import uploads

    src = inspect.getsource(uploads.direct_upload)
    close_at = src.index("db.close()")
    read_at = src.index("await file.read(")
    assert close_at < read_at, (
        "a slow 100 MB upload still pins a pool connection for its whole life"
    )


def test_the_share_is_re_checked_after_the_upload():
    """Closing the session detaches `share`, and the window it opens is real:
    the share can be revoked while the bytes are still arriving."""
    from app.routers import uploads

    src = inspect.getsource(uploads.direct_upload)
    assert src.count("get_share_or_404") == 2
    assert src.count("SHARE_NOT_ACTIVE") == 2
    assert src.count("Only the share owner can upload to it.") == 2
