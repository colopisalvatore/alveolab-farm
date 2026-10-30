import { Layers, Printer as PrinterIcon, Timer } from 'lucide-react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { formatDuration } from '../utils/date';
import type { LiveOutsidePrint } from '../utils/outsideQueue';

/** A print running on a printer with no queue row behind it (slicer,
 *  touchscreen, another tool). Read-only: there is no row to edit, reorder or
 *  stop from here, so it links to the printer card instead. */
export function OutsideQueuePrintCard({
  print,
  printerName,
  t,
}: {
  print: LiveOutsidePrint;
  printerName: string | undefined;
  t: (key: string, options?: Record<string, unknown>) => string;
}) {
  const label = t('queue.timeline.outsideQueue');
  const name = print.name || label;
  const thumbnailUrl = print.archiveId ? api.getArchiveThumbnail(print.archiveId) : null;
  const progress = print.progress != null ? Math.round(print.progress) : null;
  return (
    <div
      data-testid="outside-queue-print"
      className="p-3 sm:p-4 bg-bambu-dark-secondary rounded-lg border border-dashed border-purple-400/60"
    >
      <div className="flex items-center gap-3">
        <div className="w-12 h-12 bg-bambu-dark rounded overflow-hidden shrink-0">
          {thumbnailUrl ? (
            <img src={thumbnailUrl} alt="" className="w-full h-full object-cover" />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-bambu-gray">
              <Layers className="w-5 h-5" />
            </div>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-sm sm:text-base text-white font-medium truncate">{name}</span>
            <span className="shrink-0 text-[10px] sm:text-xs px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-700 dark:text-purple-300">
              {label}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-bambu-gray">
            <Link to="/" className="flex items-center gap-1 hover:text-white" title={t('queue.outsideQueue.openPrinter')}>
              <PrinterIcon className="w-3 h-3" />
              <span className="truncate max-w-[160px]">{printerName ?? `#${print.printerId}`}</span>
            </Link>
            {print.state === 'PAUSE' && <span className="text-yellow-600 dark:text-yellow-400">{t('queue.outsideQueue.paused')}</span>}
            {print.remainingMinutes != null && print.remainingMinutes > 0 && (
              <span className="flex items-center gap-1">
                <Timer className="w-3 h-3" />
                {t('queue.outsideQueue.remaining', { time: formatDuration(print.remainingMinutes * 60) })}
              </span>
            )}
            {progress != null && <span>{progress}%</span>}
          </div>
        </div>
      </div>
      {progress != null && (
        <div className="mt-2 h-1 bg-bambu-dark rounded-full overflow-hidden" aria-hidden>
          <div className="h-full bg-purple-400" style={{ width: `${progress}%` }} />
        </div>
      )}
    </div>
  );
}
