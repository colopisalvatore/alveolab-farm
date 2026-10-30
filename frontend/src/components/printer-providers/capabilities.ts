/**
 * ALVEOLAB Farm: which card controls a printer gets, by provider.
 *
 * Bambu printers keep every upstream Bambuddy control untouched (the card
 * already tailors those per model). Any other provider gets a control only
 * when the backend declares the matching capability — the farm never shows a
 * button the adapter cannot back (see backend printer_providers/base.py).
 */
import type { Printer, PrinterCapabilities } from '../../api/client';

/** Capability ids, mirroring the backend `Capability` enum. */
export const Capability = {
  CAMERA: 'camera',
  FILE_LIST: 'file_list',
  QUEUE_DISPATCH: 'queue_dispatch',
  JOB_START: 'job_start',
  FILE_UPLOAD: 'file_upload',
  JOB_PAUSE_RESUME: 'job_pause_resume',
  JOB_STOP: 'job_stop',
  MULTICOLOR_STATUS: 'multicolor_status',
  AMS_MANAGEMENT: 'ams_management',
} as const;

export type CapabilityId = (typeof Capability)[keyof typeof Capability];

export function isBambuPrinter(printer: Pick<Printer, 'provider'>): boolean {
  return (printer.provider ?? 'bambu') === 'bambu';
}

/**
 * React Query `select` for printer pickers that start a Bambu-only flow
 * (dispatch over FTPS, Bambu camera, AMS, K-profiles, virtual printer proxy).
 * Keeps the shared ['printers'] cache whole and just narrows that view.
 */
export function bambuPrintersOnly<T extends Pick<Printer, 'provider'>>(printers: T[]): T[] {
  return printers.filter(isBambuPrinter);
}

/**
 * True when the card may offer `capability` for this printer. While the
 * capabilities of a non-Bambu printer are still loading the answer is false:
 * a control that appears late is better than one that appears and then fails.
 */
export function providerAllows(
  printer: Pick<Printer, 'provider'>,
  capabilities: PrinterCapabilities | undefined,
  capability: CapabilityId,
): boolean {
  if (isBambuPrinter(printer)) return true;
  return capabilities?.supported.includes(capability) ?? false;
}

/** True when a library file says it was sliced for an ELEGOO Centauri 2 (the backend re-checks the file itself). */
export function isSlicedForCentauri2(slicedForModel: string | null | undefined): boolean {
  return /centauri( carbon)? 2/i.test(slicedForModel ?? '');
}
