"""ELEGOO provider — Centauri Carbon 2 (CC2) over its LAN MQTT protocol.

Modules:
  * ``protocol``     — wire constants, message builders/parsers, status cache.
  * ``normalizer``   — CC2 status -> NormalizedTelemetry -> PrinterState.
  * ``capabilities`` — what this build supports for ELEGOO models.
  * ``client``       — threaded MQTT client (read-only in M2).
  * ``discovery``    — UDP discovery on port 52700.
"""
