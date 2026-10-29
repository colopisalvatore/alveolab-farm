"""ALVEOLAB Farm printer-provider layer.

Entry points used by ``PrinterManager`` and the API:

  * :func:`capabilities_for` — capability set for a provider + model, without a
    live connection (the printers list shows it for offline machines too).
  * :func:`create_provider_client` — build the runtime client of a non-Bambu
    printer row. Bambu printers keep going through ``BambuMQTTClient`` and are
    never built here.

See ``docs/alveolab-farm/PRINTER_PROVIDER_DESIGN.md``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from backend.app.services.printer_providers.base import (
    Capability,
    CapabilitySet,
    PrinterProvider,
    PrinterProviderClient,
    parse_provider,
)

if TYPE_CHECKING:
    from backend.app.models.printer import Printer
    from backend.app.services.bambu_mqtt import PrinterState

__all__ = [
    "Capability",
    "CapabilitySet",
    "PrinterProvider",
    "PrinterProviderClient",
    "capabilities_for",
    "create_provider_client",
    "parse_provider",
]


def capabilities_for(provider: PrinterProvider | str | None, model: str | None) -> CapabilitySet:
    """Static capability set of a printer of *provider* / *model*."""
    provider = provider if isinstance(provider, PrinterProvider) else parse_provider(provider)
    if provider is PrinterProvider.BAMBU:
        from backend.app.services.printer_providers.bambu import bambu_capabilities

        return bambu_capabilities(model)
    if provider is PrinterProvider.ELEGOO:
        from backend.app.services.printer_providers.elegoo.capabilities import elegoo_capabilities

        return elegoo_capabilities(model)
    raise ValueError(f"No capability table for provider {provider!r}")


def create_provider_client(
    provider: PrinterProvider,
    printer: Printer,
    *,
    on_state_change: Callable[[PrinterState], None] | None = None,
) -> PrinterProviderClient:
    """Build the client of a non-Bambu printer row (not yet connected)."""
    if provider is PrinterProvider.ELEGOO:
        from backend.app.services.printer_providers.elegoo.client import ElegooCC2Client

        return ElegooCC2Client(
            ip_address=printer.ip_address,
            serial_number=printer.serial_number,
            access_code=printer.access_code or None,
            model=printer.model,
            on_state_change=on_state_change,
        )
    raise ValueError(f"Provider {provider!r} has no runtime client (Bambu printers use BambuMQTTClient)")
