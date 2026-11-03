"""MakerWorld upstream failures must reach the UI with their reason.

An upstream failure used to answer 502. Behind a Cloudflare tunnel the edge
swaps an origin 502 for its own "Bad gateway" page, so the provider's reason
(CAPTCHA, upstream status, S3 refusal) never reached the user, and the access
log only kept the status code. These pin the replacement: 424 with the reason
in ``detail``, and the reason written to the application log.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from backend.app.services.model_providers.makerworld.errors import MakerWorldUnavailableError

_REASON = "Bambu Lab API unexpected status 500 for profile 1108588787"


def _failing_service():
    svc = AsyncMock()
    svc.close = AsyncMock()
    svc.get_download = AsyncMock(side_effect=MakerWorldUnavailableError(_REASON))
    return svc


class TestUpstreamFailure:
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_import_answers_424_with_the_reason(self, async_client: AsyncClient, caplog):
        with (
            patch(
                "backend.app.api.routes.makerworld._build_service",
                AsyncMock(return_value=_failing_service()),
            ),
            caplog.at_level(logging.WARNING, logger="backend.app.api.routes.makerworld"),
        ):
            resp = await async_client.post(
                "/api/v1/makerworld/import",
                json={"model_id": 3523609, "profile_id": 1108588787},
            )

        assert resp.status_code == 424, resp.text
        assert resp.json()["detail"] == _REASON
        assert _REASON in caplog.text
