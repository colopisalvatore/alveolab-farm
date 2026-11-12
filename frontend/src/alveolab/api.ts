import { request } from '../api/client';

// ALVEOLAB Farm provider layer (backend/app/alveolab/routes.py).
export type PrinterProviderId = 'bambu' | 'elegoo';

/** What the farm can do with a printer; `notes` say why a capability is missing. */
export interface PrinterCapabilities {
  provider: PrinterProviderId;
  model: string | null;
  supported: string[];
  notes: Record<string, string>;
}

export interface MultiColorSlot {
  unit_id: number;
  slot_index: number;
  loaded: boolean | null;
  material: string | null;
  color_hex: string | null;  // "RRGGBB", no leading '#'
  name: string | null;
  vendor: string | null;
  // Whether the printer read the spool (RFID) or the operator declared it.
  // CC2 does not say, so it reports "unknown".
  source: 'detected' | 'declared' | 'unknown';
  active: boolean;
}

/** A fault as reported by the printer itself (code + the SDK's English description). */
export interface PrinterFault {
  code: string;
  severity: 'error' | 'warning' | 'info';
  message: string | null;
}

/** Provider-neutral telemetry snapshot (backend NormalizedTelemetry). */
export interface PrinterTelemetry {
  provider: PrinterProviderId;
  connection: string;
  job_state: string;
  firmware_version?: string | null;
  multicolor_slots?: MultiColorSlot[];
  /** CANVAS auto-refill as the printer reports it; null/absent = not reported. */
  multicolor_auto_refill?: boolean | null;
  faults?: PrinterFault[];
}

/** A filament a CANVAS slot can be set to (ELEGOO's own table, backend elegoo/canvas.py). */
export interface CanvasFilament {
  name: string;
  type: string;
  code: string;
  min_temp: number;
  max_temp: number;
  elegoo: boolean;
  generic: boolean;
}

export interface CanvasFilamentCatalogue {
  brands: string[];
  filaments: CanvasFilament[];
}

export interface CanvasSlotUpdate {
  filament_name: string;
  color: string; // "#RRGGBB"
  brand: string;
}

export interface ElegooDiscoveredPrinter {
  ip_address: string;
  serial_number: string;
  name: string | null;
  model: string | null;
  access_code_required: boolean;
  lan_only: boolean;
  provider: 'elegoo';
}

export const farmApi = {
  getPrinterCapabilities: (id: number) =>
    request<PrinterCapabilities>(`/printer-providers/printers/${id}/capabilities`),
  getPrinterTelemetry: (id: number) =>
    request<PrinterTelemetry>(`/printer-providers/printers/${id}/telemetry`),
  getCanvasFilaments: () => request<CanvasFilamentCatalogue>('/printer-providers/elegoo/canvas-filaments'),
  setCanvasSlot: (printerId: number, canvasId: number, trayId: number, data: CanvasSlotUpdate) =>
    request<{ success: boolean }>(`/printer-providers/printers/${printerId}/canvas/${canvasId}/slots/${trayId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  loadCanvasSlot: (printerId: number, canvasId: number, trayId: number) =>
    request<{ success: boolean }>(`/printer-providers/printers/${printerId}/canvas/${canvasId}/slots/${trayId}/load`, {
      method: 'POST',
    }),
  unloadCanvasSlot: (printerId: number, canvasId: number, trayId: number) =>
    request<{ success: boolean }>(
      `/printer-providers/printers/${printerId}/canvas/${canvasId}/slots/${trayId}/unload`,
      { method: 'POST' },
    ),
  setCanvasAutoRefill: (printerId: number, enabled: boolean) =>
    request<{ success: boolean }>(`/printer-providers/printers/${printerId}/canvas/auto-refill`, {
      method: 'PUT',
      body: JSON.stringify({ enabled }),
    }),
  discoverElegooPrinters: (targets: string[] = [], timeout: number = 5) =>
    request<ElegooDiscoveredPrinter[]>('/printer-providers/elegoo/discover', {
      method: 'POST',
      body: JSON.stringify({ targets, timeout }),
    }),
};
