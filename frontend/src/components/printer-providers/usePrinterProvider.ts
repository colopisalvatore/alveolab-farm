import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { Printer, PrinterCapabilities, PrinterTelemetry } from '../../api/client';
import { isBambuPrinter, providerAllows } from './capabilities';
import type { CapabilityId } from './capabilities';

export interface PrinterProviderView {
  isBambu: boolean;
  capabilities: PrinterCapabilities | undefined;
  /** Provider-neutral telemetry; only fetched for non-Bambu printers (CANVAS). */
  telemetry: PrinterTelemetry | undefined;
  can: (capability: CapabilityId) => boolean;
}

/**
 * Capabilities and telemetry of a printer's provider. Bambu printers make no
 * extra request: their card is the upstream Bambuddy one, fed by the status
 * WebSocket as before.
 */
export function usePrinterProvider(printer: Printer): PrinterProviderView {
  const isBambu = isBambuPrinter(printer);
  const { data: capabilities } = useQuery({
    queryKey: ['printerCapabilities', printer.id],
    queryFn: () => api.getPrinterCapabilities(printer.id),
    enabled: !isBambu,
    staleTime: 5 * 60 * 1000,
  });
  const { data: telemetry } = useQuery({
    queryKey: ['printerTelemetry', printer.id],
    queryFn: () => api.getPrinterTelemetry(printer.id),
    enabled: !isBambu,
    refetchInterval: 10000,
  });
  return {
    isBambu,
    capabilities,
    telemetry,
    can: (capability) => providerAllows(printer, capabilities, capability),
  };
}
