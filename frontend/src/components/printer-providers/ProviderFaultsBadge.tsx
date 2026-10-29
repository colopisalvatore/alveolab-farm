/**
 * Fault badge for printers that do not speak Bambu HMS (ELEGOO CC2).
 *
 * Shows the faults the printer itself reports in its status
 * (machine_status.exception_status, exception.exception_code, emergency stop),
 * with the SDK's description. Nothing is inferred: no telemetry yet means no
 * badge, not a green one.
 */
import { useTranslation } from 'react-i18next';
import { AlertTriangle } from 'lucide-react';
import type { PrinterFault } from '../../api/client';

export function ProviderFaultsBadge({ faults }: { faults: PrinterFault[] | undefined }) {
  const { t } = useTranslation();
  if (faults === undefined) return null;

  const hasError = faults.some((f) => f.severity === 'error');
  const title =
    faults.length === 0
      ? t('farm.faultsNone')
      : [t('farm.faultsTitle'), ...faults.map((f) => (f.message ? `${f.code}: ${f.message}` : f.code))].join('\n');

  return (
    <span
      role="status"
      data-testid="provider-faults"
      title={title}
      aria-label={title}
      className={`flex items-center gap-1 px-2 py-1 rounded-full text-xs ${
        faults.length === 0
          ? 'bg-status-ok/20 text-status-ok'
          : hasError
            ? 'bg-status-error/20 text-status-error'
            : 'bg-status-warning/20 text-status-warning'
      }`}
    >
      <AlertTriangle className="w-[var(--pc-i3,0.75rem)] h-[var(--pc-i3,0.75rem)]" />
      {faults.length === 0 ? 'OK' : faults.length}
    </span>
  );
}
