"""/api/admin/files — cross-user file history inventory."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...middleware.errors import AppError
from ...models.file import File, FileState
from ...models.share import Share
from ...models.user import User
from ...schemas.file_admin import (
    AdminFileItem,
    AdminFileListResponse,
    FileUploaderRef,
)
from ...services import file_admin as file_admin_svc

router = APIRouter()


@router.get("/files", response_model=AdminFileListResponse)
def admin_list_files(
    q: str = Query("", max_length=255),
    state: str | None = Query(None),
    uploader_id: int | None = Query(None, ge=1),
    share_state: str | None = Query(None),
    orphaned: bool = Query(False),
    include_inactive: bool = Query(False),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    sort: str = Query("uploaded_at"),
    direction: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> AdminFileListResponse:
    rows, total = file_admin_svc.list_all_files(
        db,
        q=q,
        state=state,
        uploader_id=uploader_id,
        share_state=share_state,
        orphaned=orphaned,
        include_inactive=include_inactive,
        from_ts=from_ts,
        to_ts=to_ts,
        sort=sort,
        direction=direction,
        page=page,
        page_size=page_size,
    )
    items = [
        AdminFileItem(
            file_id=r["file_id"],
            filename=r["filename"],
            size_bytes=r["size_bytes"],
            state=r["state"],
            share_id=r["share_id"],
            share_subject=r["share_subject"],
            share_state=r["share_state"],
            uploader=FileUploaderRef(**r["uploader"]),
            recipients_summary=r["recipients_summary"],
            uploaded_at=r["uploaded_at"],
            last_downloaded_at=r["last_downloaded_at"],
            download_count=r["download_count"],
            is_orphaned=r["is_orphaned"],
        )
        for r in rows
    ]
    return AdminFileListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


@router.post("/files/{file_id}/reclaim", status_code=status.HTTP_204_NO_CONTENT)
def admin_reclaim_orphan(
    file_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> Response:
    """Free an orphaned file's bytes + the uploader's quota now, without
    waiting for the grace-window cron. Refuses anything that isn't an orphan
    (so active-share files can't be deleted through here)."""
    file = db.query(File).filter(File.id == file_id).one_or_none()
    if file is None:
        raise AppError(404, "FILE_NOT_FOUND", "File not found.")
    share = db.query(Share).filter(Share.id == file.share_id).one_or_none()
    if share is None or not file_admin_svc.is_orphan(file, share):
        raise AppError(
            409,
            "NOT_ORPHANED",
            "Only files whose share is revoked/deleted (and still on disk) can be reclaimed.",
        )

    from ...services import file as file_svc

    file_svc.hard_delete(
        db, file=file, reason="admin_reclaim", actor_user_id=admin.id, request=request
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_file(
    file_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> Response:
    """Admin hard-deletes any file's bytes from the File History view. Frees the
    uploader's quota, audits `file_deleted` with the admin as actor, and
    auto-revokes the parent share if this was its last live file. Idempotent on
    already-deleted files."""
    file = db.query(File).filter(File.id == file_id).one_or_none()
    if file is None:
        raise AppError(404, "FILE_NOT_FOUND", "File not found.")
    if file.state == FileState.deleted:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    from ...services import file as file_svc

    share_id = file.share_id
    file_svc.hard_delete(
        db, file=file, reason="admin_delete", actor_user_id=admin.id, request=request
    )
    file_svc.revoke_share_if_empty(
        db,
        share_id=share_id,
        just_deleted_file_id=file.id,
        actor_user_id=admin.id,
        request=request,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
