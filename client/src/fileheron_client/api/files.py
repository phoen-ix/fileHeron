"""Download control signals.

The single-stream ``download_file`` that used to live here was superseded by
``download_resumable.download_file_resumable`` - which has its own
single-stream leg with Range resume, a checkpoint and the Mark-of-the-Web -
and had no caller left outside one test. These two exceptions are the contract
the UI and the downloader still share.
"""
from __future__ import annotations


class DownloadCancelled(Exception):
    """Raised when a download is aborted via its cancel Event. Distinct from
    transport errors so the segmented downloader neither retries it nor falls
    back to a single stream - it just unwinds + cleans up."""


class DownloadPaused(Exception):
    """Raised when a download is paused via its pause Event. Unlike
    ``DownloadCancelled`` the partial ``.part`` file + checkpoint are KEPT so
    the transfer can be resumed later. Never retried."""
