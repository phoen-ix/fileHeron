"""/api/admin/analytics - usage + observability dashboard data.

Read-only. Most numbers are computed live from persisted timestamps; only the
storage-growth trend reads the nightly `analytics_snapshots` rows. See
`services/analytics.py`.
"""
from __future__ import annotations

import csv
import io
from collections.abc import Iterator

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...dependencies import get_current_admin, get_db
from ...models.user import User
from ...services import analytics as analytics_svc

router = APIRouter()


@router.get("/analytics")
def get_analytics(
    days: int = Query(30, ge=1, le=90),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> dict:
    """The analytics bundle for the last `days` days (storage trend + live
    share/download/AV series + current top-N + quota warnings)."""
    return analytics_svc.compute_analytics(db, days=days)


@router.get("/analytics/export.csv")
def export_analytics_csv(
    days: int = Query(30, ge=1, le=90),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
) -> StreamingResponse:
    """Flatten the bundle to a (section, key, value) CSV. The payload is already
    bounded (≤90 day-rows + top-10s), so we render it in one pass."""
    b = analytics_svc.compute_analytics(db, days=days)

    def _rows() -> Iterator[bytes]:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["section", "key", "value"])
        for s in b["storage_trend"]:
            w.writerow(["storage_bytes", s["date"], s["storage_bytes"]])
        for s in b["shares_created"]:
            w.writerow(["shares_created", s["date"], s["count"]])
        for s in b["downloads"]:
            w.writerow(["downloads", s["date"], s["count"]])
        for s in b["av_quarantines"]:
            w.writerow(["av_quarantines", s["date"], s["count"]])
        for k, v in b["file_states"].items():
            w.writerow(["file_state", k, v])
        for u in b["top_uploaders"]:
            w.writerow(["top_uploader", u["email"], u["bytes"]])
        for sh in b["top_shares"]:
            w.writerow(["top_share", sh["share_id"], sh["downloads"]])
        for q in b["quota_warnings"]:
            w.writerow(["quota_warning", q["email"], f'{q["pct"]}%'])
        yield buf.getvalue().encode("utf-8")

    return StreamingResponse(
        _rows(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="analytics.csv"'},
    )
