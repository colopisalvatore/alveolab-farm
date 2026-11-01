import { useQueries } from '@tanstack/react-query';
import { Activity, Clock3, Layers3, Plus, Printer as PrinterIcon, WifiOff } from 'lucide-react';
import { Link } from 'react-router-dom';
import type { TFunction } from 'i18next';
import { api, type Printer, type PrinterStatus, type PrintQueueItem } from '../api/client';
import { formatDuration, formatETA, type TimeFormat } from '../utils/date';

interface FarmStatusGridProps {
  printers: Printer[];
  queueItems: PrintQueueItem[];
  t: TFunction;
  timeFormat: TimeFormat;
}

function isPrinting(state: string | null | undefined): boolean {
  return ['running', 'printing', 'pause', 'paused'].includes((state ?? '').toLowerCase());
}

function isReady(state: string | null | undefined): boolean {
  return ['idle', 'ready', 'finish', 'finished', 'complete', 'completed'].includes((state ?? '').toLowerCase());
}

function loadedMaterials(status: PrinterStatus | undefined): string[] {
  if (!status) return [];
  const trays = [...status.ams.flatMap((unit) => unit.tray), ...status.vt_tray];
  return [...new Set(trays
    .filter((tray) => tray.tray_type && tray.tray_type.trim() && tray.state !== 9)
    .map((tray) => tray.tray_type!.trim()))];
}

export function FarmStatusGrid({ printers, queueItems, t, timeFormat }: FarmStatusGridProps) {
  const statusQueries = useQueries({
    queries: printers.map((printer) => ({
      queryKey: ['printerStatus', printer.id],
      queryFn: () => api.getPrinterStatus(printer.id),
      refetchInterval: 5000,
      staleTime: 0,
    })),
  });

  const connectedCount = statusQueries.filter((query) => query.data?.connected).length;

  return (
    <section className="mb-6" aria-label={t('queue.title')}>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
        <div className="flex items-center gap-2">
          <PrinterIcon className="w-5 h-5 text-bambu-green" />
          <h2 className="text-base sm:text-lg font-semibold text-white">{t('queue.title')}</h2>
          <span className="text-xs text-bambu-gray">
            {connectedCount}/{printers.length} {t('printers.connection.connected').toLowerCase()}
          </span>
          <span className="flex items-center gap-1 text-[11px] text-bambu-green" title={t('printers.connection.connected')}>
            <Activity className="w-3.5 h-3.5" /> 5s
          </span>
        </div>
        <Link
          to="/files"
          className="inline-flex items-center gap-2 rounded-lg bg-bambu-green px-3 py-2 text-sm font-medium text-bambu-dark hover:bg-bambu-green-light transition-colors"
          title={t('queue.printQueuedWillStartWhenIdle')}
        >
          <Plus className="w-4 h-4" />
          {t('queue.farm.addJob')}
        </Link>
      </div>

      {printers.length === 0 ? (
        <div className="rounded-xl border border-bambu-dark-tertiary bg-bambu-dark-secondary p-5 text-sm text-bambu-gray">
          {t('printers.status.unknown')}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {printers.map((printer, index) => {
            const query = statusQueries[index];
            const status = query.data;
            const busy = Boolean(status?.connected && isPrinting(status.state));
            const waitingForClear = Boolean(status?.connected && status.awaiting_plate_clear);
            const ready = Boolean(status?.connected && isReady(status.state));
            const activeJob = queueItems.find((item) => item.printer_id === printer.id && item.status === 'printing');
            const assignedCount = queueItems.filter((item) =>
              item.printer_id === printer.id && ['pending', 'printing'].includes(item.status),
            ).length;
            const plateLabel = !status
              ? t('printers.status.unknown')
              : !status.connected
                ? t('printers.connection.offline')
                : waitingForClear
                  ? t('printers.plateStatus.notCleared')
                  : busy
                    ? t('printers.plateStatus.inUse')
                    : ready
                      ? t('printers.plateStatus.cleared')
                      : t('printers.status.unknown');
            const stateLabel = !status
              ? t('printers.status.unknown')
              : !status.connected
                ? t('printers.status.offline')
                : waitingForClear
                  ? t('printers.status.finished')
                  : busy
                    ? (status.state?.toLowerCase() === 'paused' || status.state?.toLowerCase() === 'pause'
                      ? t('printers.status.paused')
                      : t('printers.status.printing'))
                    : ready
                      ? t('printers.status.available')
                      : ['error', 'failed', 'fail'].includes((status.state ?? '').toLowerCase())
                        ? t('printers.status.error')
                        : t('printers.status.unknown');
            const materials = loadedMaterials(status);
            const jobName = status?.subtask_name || status?.current_print || activeJob?.archive_name || activeJob?.library_file_name;

            return (
              <article key={printer.id} className="rounded-xl border border-bambu-dark-tertiary bg-bambu-dark-secondary p-4 min-w-0">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="truncate font-semibold text-white">{printer.name}</h3>
                    <p className="truncate text-xs text-bambu-gray">
                      {[printer.model, printer.location].filter(Boolean).join(' · ')}
                    </p>
                  </div>
                  <span className={`shrink-0 rounded-full px-2 py-1 text-xs font-medium ${
                    !status?.connected
                      ? 'bg-red-500/15 text-red-300'
                      : busy
                        ? 'bg-blue-500/15 text-blue-300'
                      : ready
                        ? 'bg-bambu-green/15 text-bambu-green'
                        : 'bg-amber-500/15 text-amber-300'
                  }`}>
                    {stateLabel}
                  </span>
                </div>

                <div className={`mt-4 flex items-center gap-1.5 text-sm ${waitingForClear ? 'text-amber-300' : status?.connected ? 'text-white' : 'text-bambu-gray'}`}>
                  {!status?.connected ? <WifiOff className="w-4 h-4" /> : <PrinterIcon className="w-4 h-4" />}
                  <span>{plateLabel}</span>
                </div>

                {busy && status?.progress != null && (
                  <div className="mt-3">
                    <div className="mb-1 flex justify-between text-xs text-bambu-gray">
                      <span className="truncate pr-2">{jobName || t('queue.sections.currentlyPrinting')}</span>
                      <span>{Math.round(status.progress)}%</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-bambu-dark-tertiary">
                      <div className="h-full rounded-full bg-bambu-green transition-all" style={{ width: `${Math.max(0, Math.min(100, status.progress))}%` }} />
                    </div>
                  </div>
                )}

                {!busy && jobName && (
                  <p className="mt-3 truncate text-sm text-white" title={jobName}>{jobName}</p>
                )}

                <div className="mt-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-t border-bambu-dark-tertiary pt-3 text-xs text-bambu-gray">
                  <span className="flex items-center gap-1.5">
                    <Layers3 className="w-3.5 h-3.5" />
                    {t('printers.queue.inQueue', { count: assignedCount })}
                  </span>
                  {busy && status?.remaining_time != null && (
                    <span className="flex items-center gap-1.5">
                      <Clock3 className="w-3.5 h-3.5" />
                      ETA {formatETA(status.remaining_time, timeFormat, t)} · {formatDuration(status.remaining_time * 60)}
                    </span>
                  )}
                </div>

                {materials.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {materials.map((material) => (
                      <span key={material} className="rounded-md bg-bambu-dark px-2 py-1 text-[11px] text-bambu-gray">
                        {material}
                      </span>
                    ))}
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
