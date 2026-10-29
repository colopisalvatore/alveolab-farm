"""ALVEOLAB Farm provider layer (M1): contracts, Bambu normalisation, manager wiring.

The Bambu path must behave exactly as before; these tests pin both the new
provider vocabulary and the guarantees that keep a non-Bambu client away from
Bambu-only call sites.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.bambu_mqtt import HMSError, PrinterState
from backend.app.services.printer_manager import PrinterManager
from backend.app.services.printer_providers import capabilities_for, create_provider_client
from backend.app.services.printer_providers.bambu import bambu_job_state, normalize_bambu_state
from backend.app.services.printer_providers.base import (
    Capability,
    CapabilitySet,
    ConnectionStatus,
    JobState,
    NormalizedTelemetry,
    PrinterProvider,
    PrinterProviderClient,
    SlotDataSource,
    parse_provider,
)


class TestParseProvider:
    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_missing_value_is_bambu(self, value):
        """Rows that predate printers.provider are all Bambu printers."""
        assert parse_provider(value) is PrinterProvider.BAMBU

    def test_non_string_is_bambu(self):
        assert parse_provider(MagicMock()) is PrinterProvider.BAMBU

    @pytest.mark.parametrize(("value", "expected"), [("elegoo", "elegoo"), (" ELEGOO ", "elegoo"), ("bambu", "bambu")])
    def test_known_values(self, value, expected):
        assert parse_provider(value).value == expected

    def test_unknown_string_is_an_error(self):
        """Never fall back to Bambu for a value we don't recognise."""
        with pytest.raises(ValueError, match="Unknown printer provider"):
            parse_provider("prusa")


class TestCapabilities:
    def test_bambu_supports_everything_bambuddy_does(self):
        caps = capabilities_for("bambu", "P1S")
        for cap in (
            Capability.STATUS,
            Capability.JOB_START,
            Capability.JOB_PAUSE_RESUME,
            Capability.QUEUE_DISPATCH,
            Capability.AUTO_ARCHIVE,
            Capability.AMS_MANAGEMENT,
        ):
            assert caps.supports(cap), cap

    def test_bambu_chamber_temperature_follows_model(self):
        assert not capabilities_for("bambu", "P1S").supports(Capability.CHAMBER_TEMPERATURE)
        assert "chamber" in capabilities_for("bambu", "P1S").notes[Capability.CHAMBER_TEMPERATURE]
        assert capabilities_for("bambu", "X1C").supports(Capability.CHAMBER_TEMPERATURE)

    def test_to_dict_is_json_friendly_and_sorted(self):
        caps = CapabilitySet(
            provider=PrinterProvider.ELEGOO,
            model="CC2",
            supported=frozenset({Capability.TEMPERATURES, Capability.STATUS}),
            notes={Capability.CAMERA: "later"},
        )
        assert caps.to_dict() == {
            "provider": "elegoo",
            "model": "CC2",
            "supported": ["status", "temperatures"],
            "notes": {"camera": "later"},
        }


class TestBambuNormalization:
    def test_job_state_mapping(self):
        assert bambu_job_state("RUNNING") is JobState.PRINTING
        assert bambu_job_state("PAUSE") is JobState.PAUSED
        assert bambu_job_state("FINISH") is JobState.FINISHED
        assert bambu_job_state("FAILED") is JobState.FAILED
        assert bambu_job_state("PREPARE") is JobState.PREPARING
        assert bambu_job_state("IDLE") is JobState.IDLE

    @pytest.mark.parametrize("value", [None, "", "unknown", "SOMETHING_NEW"])
    def test_unrecognised_state_is_unknown(self, value):
        assert bambu_job_state(value) is JobState.UNKNOWN

    def test_running_state(self):
        state = PrinterState(
            connected=True,
            state="RUNNING",
            subtask_name="Svuotatasche 220mm",
            progress=42,
            remaining_time=73,
            layer_num=120,
            total_layers=300,
            temperatures={"nozzle": 219.5, "nozzle_target": 220, "bed": 60.1, "bed_target": 60},
            cooling_fan_speed=100,
            firmware_version="01.08.00.00",
        )
        t = normalize_bambu_state(state, "P1S")
        assert t.provider is PrinterProvider.BAMBU
        assert t.connection is ConnectionStatus.ONLINE
        assert t.job_state is JobState.PRINTING
        assert t.job_active
        assert t.filename == "Svuotatasche 220mm"
        assert t.progress_percent == 42.0
        assert t.remaining_seconds == 73 * 60  # Bambu reports minutes
        assert (t.current_layer, t.total_layers) == (120, 300)
        assert (t.nozzle.current, t.nozzle.target) == (219.5, 220.0)
        assert (t.bed.current, t.bed.target) == (60.1, 60.0)
        assert t.chamber.current is None  # not reported -> None, never 0
        assert t.fans_percent == {"part_cooling": 100}
        assert t.firmware_version == "01.08.00.00"
        assert t.model == "P1S"

    def test_disconnect_keeps_job_state(self):
        """Link loss is OFFLINE, not FINISHED: the job state is left as last seen."""
        t = normalize_bambu_state(PrinterState(connected=False, state="RUNNING"))
        assert t.connection is ConnectionStatus.OFFLINE
        assert t.job_state is JobState.PRINTING

    def test_hms_faults(self):
        state = PrinterState(
            connected=True,
            hms_errors=[
                HMSError(code="0300_0100", attr=0, module=3, severity=2, description="Nozzle clog"),
                HMSError(code="0500_4004", attr=0, module=5, severity=1),
            ],
        )
        faults = normalize_bambu_state(state).faults
        assert [(f.code, f.severity, f.message) for f in faults] == [
            ("0300_0100", "warning", "Nozzle clog"),
            ("0500_4004", "error", None),
        ]

    def test_ams_slots_detected_vs_declared(self):
        state = PrinterState(
            connected=True,
            raw_data={
                "ams": [
                    {
                        "id": "0",
                        "tray": [
                            {"id": "0", "tray_type": "PLA", "tray_color": "FF0000FF", "tag_uid": "A1B2C3D4E5F60708"},
                            {"id": "1", "tray_type": "PETG", "tray_color": "00FF00FF", "tag_uid": "0000000000000000"},
                            {"id": "2", "tray_type": ""},
                            {"id": "3"},
                        ],
                    }
                ]
            },
        )
        slots = normalize_bambu_state(state).multicolor_slots
        assert [(s.slot_index, s.material, s.color_hex, s.source, s.loaded) for s in slots] == [
            (0, "PLA", "FF0000", SlotDataSource.DETECTED, True),
            (1, "PETG", "00FF00", SlotDataSource.DECLARED, True),
            (2, None, None, SlotDataSource.DECLARED, False),
            (3, None, None, SlotDataSource.DECLARED, None),
        ]

    def test_normalization_does_not_mutate_state(self):
        state = PrinterState(connected=True, state="IDLE", temperatures={"nozzle": 25})
        before = repr(state)
        normalize_bambu_state(state)
        assert repr(state) == before


class _FakeProviderClient:
    """Minimal PrinterProviderClient used to exercise PrinterManager wiring."""

    provider = PrinterProvider.ELEGOO

    def __init__(self, connected: bool = True):
        self._state = PrinterState(connected=connected, state="IDLE")
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.staleness_checks = 0

    @property
    def state(self):
        return self._state

    @property
    def capabilities(self):
        return capabilities_for("elegoo", "Centauri Carbon 2")

    def connect(self):
        self.connect_calls += 1

    def disconnect(self, timeout: float = 0):
        self.disconnect_calls += 1

    def check_staleness(self) -> bool:
        self.staleness_checks += 1
        return self._state.connected

    def telemetry(self):
        return NormalizedTelemetry(provider=PrinterProvider.ELEGOO)


class TestPrinterManagerProviders:
    @pytest.fixture
    def manager(self):
        return PrinterManager()

    @pytest.fixture
    def elegoo_printer(self):
        printer = MagicMock()
        printer.id = 7
        printer.name = "Centauri"
        printer.provider = "elegoo"
        printer.ip_address = "192.0.2.10"
        printer.serial_number = "CC2-TEST"
        printer.access_code = ""
        printer.model = "Centauri Carbon 2"
        return printer

    def test_fake_client_satisfies_protocol(self):
        assert isinstance(_FakeProviderClient(), PrinterProviderClient)

    @pytest.mark.asyncio
    async def test_elegoo_printer_never_builds_a_bambu_client(self, manager, elegoo_printer):
        fake = _FakeProviderClient()
        with (
            patch("backend.app.services.printer_manager.BambuMQTTClient") as bambu_cls,
            patch("backend.app.services.printer_providers.create_provider_client", return_value=fake) as factory,
            patch("backend.app.services.printer_manager.asyncio.sleep"),
        ):
            assert await manager.connect_printer(elegoo_printer) is True
        bambu_cls.assert_not_called()
        factory.assert_called_once()
        assert factory.call_args.args[0] is PrinterProvider.ELEGOO
        assert fake.connect_calls == 1
        assert manager.get_provider(7) is PrinterProvider.ELEGOO
        assert manager.get_provider_client(7) is fake

    @pytest.mark.asyncio
    async def test_get_client_stays_bambu_only(self, manager, elegoo_printer):
        """~70 call sites send Bambu MQTT commands to get_client()'s result."""
        with (
            patch("backend.app.services.printer_providers.create_provider_client", return_value=_FakeProviderClient()),
            patch("backend.app.services.printer_manager.asyncio.sleep"),
        ):
            await manager.connect_printer(elegoo_printer)
        assert manager.get_client(7) is None
        assert manager.start_print(7, "x.3mf") is False
        assert manager.stop_print(7) is False

    def test_status_readers_see_provider_clients(self, manager):
        fake = _FakeProviderClient()
        manager._provider_clients[7] = fake
        manager._providers[7] = PrinterProvider.ELEGOO
        bambu = MagicMock()
        bambu.state = PrinterState(connected=True, state="RUNNING")
        bambu.check_staleness.return_value = True
        manager._clients[1] = bambu

        assert manager.get_status(7) is fake.state
        assert manager.is_connected(7) is True
        assert set(manager.get_all_statuses()) == {1, 7}
        assert fake.staleness_checks == 3

    def test_disconnect_provider_client(self, manager):
        fake = _FakeProviderClient()
        manager._provider_clients[7] = fake
        manager._providers[7] = PrinterProvider.ELEGOO
        manager._models[7] = "Centauri Carbon 2"
        manager.disconnect_printer(7)
        assert fake.disconnect_calls == 1
        assert manager.get_status(7) is None
        assert manager.get_provider(7) is None
        assert manager.get_model(7) is None

    def test_disconnect_all_covers_both_maps(self, manager):
        fake = _FakeProviderClient()
        manager._provider_clients[7] = fake
        bambu = MagicMock()
        manager._clients[1] = bambu
        manager.disconnect_all()
        assert fake.disconnect_calls == 1
        bambu.disconnect.assert_called_once()
        assert manager._clients == {} and manager._provider_clients == {}

    def test_capabilities(self, manager):
        manager._provider_clients[7] = _FakeProviderClient()
        manager._providers[7] = PrinterProvider.ELEGOO
        manager._providers[1] = PrinterProvider.BAMBU
        manager._models[1] = "P1S"
        assert not manager.get_capabilities(7).supports(Capability.QUEUE_DISPATCH)
        assert manager.get_capabilities(1).supports(Capability.QUEUE_DISPATCH)
        assert manager.get_capabilities(99) is None

    @pytest.mark.asyncio
    async def test_bambu_connect_path_unchanged(self, manager):
        """A legacy row (provider None) still builds a BambuMQTTClient with the same arguments."""
        printer = MagicMock()
        printer.id = 1
        printer.provider = None
        printer.ip_address = "192.0.2.20"
        printer.serial_number = "00M09A123456789"
        printer.access_code = "12345678"
        printer.model = "P1S"
        with (
            patch("backend.app.services.printer_manager.BambuMQTTClient") as bambu_cls,
            patch("backend.app.services.printer_providers.create_provider_client") as factory,
            patch("backend.app.services.printer_manager.asyncio.sleep"),
        ):
            bambu_cls.return_value.state.connected = True
            assert await manager.connect_printer(printer) is True
        factory.assert_not_called()
        kwargs = bambu_cls.call_args.kwargs
        assert (kwargs["ip_address"], kwargs["serial_number"], kwargs["access_code"], kwargs["model"]) == (
            "192.0.2.20",
            "00M09A123456789",
            "12345678",
            "P1S",
        )
        assert manager.get_provider(1) is PrinterProvider.BAMBU
        assert manager.get_client(1) is bambu_cls.return_value


def test_create_provider_client_rejects_bambu():
    with pytest.raises(ValueError, match="BambuMQTTClient"):
        create_provider_client(PrinterProvider.BAMBU, MagicMock())
