"""File upload to an ELEGOO Centauri Carbon 2 over HTTP (M4).

Same transfer as the official SDK (``elegoo_fdm_cc2_http_transfer.cpp:55-290``):
``PUT http://<ip>/upload`` with the raw bytes in chunks of at most 1 MiB, each
chunk carrying ``Content-Range``, the file name, the MD5 of the whole file and
the access code as ``X-Token``. The printer answers ``{"error_code": 0}`` per
chunk; anything else stops the upload.

An upload only stores a file on the printer, it never starts a print. A failed
chunk is not retried here: the caller decides, and re-sending the whole file
under the same name overwrites it.

The access code travels in a header over plain HTTP, like the SDK does; it is
never logged.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from backend.app.services.printer_providers.elegoo import protocol as p

logger = logging.getLogger(__name__)

UPLOAD_ERROR_AUTH = "auth_failed"
UPLOAD_ERROR_BUSY = "printer_busy"
UPLOAD_ERROR_REJECTED = "rejected"
UPLOAD_ERROR_UNREACHABLE = "unreachable"
UPLOAD_ERROR_BAD_FILE = "bad_file"

# LAN timeouts of the SDK client: 60 s to connect, 180 s per chunk.
CONNECT_TIMEOUT_SECONDS = 60.0
CHUNK_TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True)
class UploadResult:
    success: bool
    filename: str
    size: int = 0
    md5: str | None = None
    uploaded_bytes: int = 0
    reason: str | None = None  # one of UPLOAD_ERROR_*
    message: str | None = None
    printer_error_code: int | None = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "filename": self.filename,
            "size": self.size,
            "md5": self.md5,
            "uploaded_bytes": self.uploaded_bytes,
            "reason": self.reason,
            "message": self.message,
            "printer_error_code": self.printer_error_code,
        }


def file_md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - integrity check required by the printer, not security
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(p.UPLOAD_CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def _interpret(response: httpx.Response) -> tuple[str | None, str | None, int | None]:
    """(reason, message, printer_error_code) of one chunk reply; reason None = accepted."""
    if response.status_code == 401:
        return UPLOAD_ERROR_AUTH, "access code refused (HTTP 401)", None
    if response.status_code == 403:
        return UPLOAD_ERROR_AUTH, "access denied (HTTP 403)", None
    if response.status_code == 429:
        return UPLOAD_ERROR_BUSY, "printer busy (HTTP 429)", None
    if not 200 <= response.status_code < 300:
        return UPLOAD_ERROR_REJECTED, f"HTTP {response.status_code}", None
    try:
        body = response.json()
    except ValueError:
        return UPLOAD_ERROR_REJECTED, "reply is not JSON", None
    code = body.get("error_code") if isinstance(body, dict) else None
    if not isinstance(code, int):
        return UPLOAD_ERROR_REJECTED, "reply has no error_code", None
    if code == 0:
        return None, None, 0
    if code == 1000:
        return UPLOAD_ERROR_AUTH, p.describe_error_code(code), code
    return UPLOAD_ERROR_REJECTED, p.describe_error_code(code), code


async def upload_file(
    ip_address: str,
    local_path: Path,
    filename: str,
    access_code: str | None,
    *,
    chunk_size: int = p.UPLOAD_CHUNK_BYTES,
    on_progress: Callable[[int, int], None] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> UploadResult:
    """Upload *local_path* to the printer as *filename*. Never starts a print."""
    try:
        p.check_machine_filename(filename)
    except ValueError as exc:
        return UploadResult(False, filename, reason=UPLOAD_ERROR_BAD_FILE, message=str(exc))
    local_path = Path(local_path)
    if not local_path.is_file():
        return UploadResult(False, filename, reason=UPLOAD_ERROR_BAD_FILE, message="local file not found")
    total = local_path.stat().st_size
    if total == 0:
        return UploadResult(False, filename, reason=UPLOAD_ERROR_BAD_FILE, message="file is empty")
    chunk_size = max(1, min(chunk_size, p.UPLOAD_CHUNK_BYTES))
    md5 = file_md5(local_path)
    token = access_code or p.DEFAULT_ACCESS_CODE

    timeout = httpx.Timeout(CHUNK_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
    sent = 0
    logger.info("[ELEGOO %s] uploading %s (%d bytes)", ip_address, filename, total)
    async with httpx.AsyncClient(
        base_url=f"http://{ip_address}:{p.UPLOAD_PORT}", timeout=timeout, transport=transport
    ) as client:
        with local_path.open("rb") as handle:
            while sent < total:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                end = sent + len(chunk) - 1
                headers = {
                    "Content-Type": "application/octet-stream",
                    "Content-Range": f"bytes {sent}-{end}/{total}",
                    "X-File-Name": filename,
                    "X-File-MD5": md5,
                    "X-Token": token,
                }
                try:
                    response = await client.put(p.UPLOAD_PATH, content=chunk, headers=headers)
                except httpx.HTTPError as exc:
                    logger.warning(
                        "[ELEGOO %s] upload of %s failed at byte %d: %s", ip_address, filename, sent, type(exc).__name__
                    )
                    return UploadResult(False, filename, total, md5, sent, UPLOAD_ERROR_UNREACHABLE, type(exc).__name__)
                reason, message, code = _interpret(response)
                if reason is not None:
                    logger.warning(
                        "[ELEGOO %s] upload of %s refused at byte %d: %s", ip_address, filename, sent, message
                    )
                    return UploadResult(False, filename, total, md5, sent, reason, message, code)
                sent += len(chunk)
                if on_progress is not None:
                    on_progress(sent, total)
    logger.info("[ELEGOO %s] uploaded %s", ip_address, filename)
    return UploadResult(True, filename, total, md5, sent)
