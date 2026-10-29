"""UDP LAN discovery of ELEGOO Centauri Carbon 2 printers.

Broadcasts ``{"id": 0, "method": 7000}`` to port 52700 and collects replies
(src/lan/discovery/printer_discovery.cpp:433-557,
src/lan/adapters/elegoo_fdm_cc2/elegoo_fdm_cc2_discovery_strategy.cpp). A probe
is a read-only query: it changes nothing on the printer.

``targets`` lets the caller probe one IP directly (unicast also answers, per the
community docs) — useful when the farm runs in Docker bridge mode where
broadcast does not leave the container.
"""

from __future__ import annotations

import asyncio
import logging
import socket

from backend.app.services.printer_providers.elegoo.protocol import (
    DISCOVERY_PORT,
    CC2DiscoveryResult,
    discovery_request,
    parse_discovery_response,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 5.0  # DiscoveryConfig.timeoutMs (printer_discovery.h:58-61)
MAX_TIMEOUT_SECONDS = 30.0


class _CollectorProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.results: dict[str, CC2DiscoveryResult] = {}

    def datagram_received(self, data: bytes, addr) -> None:
        result = parse_discovery_response(data, addr[0])
        if result is not None:
            # Keyed by serial: a printer answering on two interfaces is one printer.
            self.results[result.serial_number] = result

    def error_received(self, exc: Exception) -> None:
        logger.debug("ELEGOO discovery socket error: %s", exc)


async def discover_cc2(
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    targets: list[str] | None = None,
) -> list[CC2DiscoveryResult]:
    """Return the CC2 printers that answered within *timeout* seconds."""
    timeout = max(0.5, min(float(timeout), MAX_TIMEOUT_SECONDS))
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        _CollectorProtocol,
        local_addr=("0.0.0.0", 0),
        allow_broadcast=True,
        family=socket.AF_INET,
    )
    try:
        message = discovery_request()
        for host in targets or ["255.255.255.255"]:
            try:
                transport.sendto(message, (host, DISCOVERY_PORT))
            except OSError as exc:
                logger.info("ELEGOO discovery: cannot send to %s: %s", host, exc)
        await asyncio.sleep(timeout)
    finally:
        transport.close()
    return sorted(protocol.results.values(), key=lambda r: (r.ip_address, r.serial_number))
