import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { farmApi } from '../api';
import { formatDuration } from '../../utils/date';

const STATUS_STYLE: Record<string, string> = {
  completed: 'text-bambu-green',
  failed: 'text-red-600 dark:text-red-400',
  cancelled: 'text-yellow-600 dark:text-yellow-400',
  aborted: 'text-yellow-600 dark:text-yellow-400',
};

/** Finished prints no queue row owns, read from their archives, listed under the queue's own history. */
export function OutsideQueueHistory({ printerId }: { printerId?: number | null }) {
  const { t } = useTranslation();
  const { data } = useQuery({
    queryKey: ['queue', 'outside-prints', printerId ?? null],
    queryFn: () => farmApi.getOutsideQueuePrints(printerId || undefined),
    refetchInterval: 30000,
  });
  if (!data || data.length === 0) return null;
  return (
    <div className="mt-6" data-testid="outside-queue-history">
      <h2 className="text-base sm:text-lg font-semibold text-white mb-3">{t('farm.outsideQueue.historyTitle')}</h2>
      <div className="space-y-1">
        {data.map((print) => {
          const when = print.completed_at ?? print.created_at;
          return (
            <div
              key={print.archive_id}
              className="px-3 py-2 bg-bambu-dark-secondary rounded-lg flex flex-wrap items-center gap-x-3 gap-y-1 text-sm"
            >
              <span className="text-white font-medium truncate min-w-0 flex-1">{print.name}</span>
              <span className={`text-xs ${STATUS_STYLE[print.status] ?? 'text-bambu-gray'}`}>{print.status}</span>
              <span className="text-xs text-bambu-gray">{print.printer_name ?? ''}</span>
              {print.print_time_seconds != null && (
                <span className="text-xs text-bambu-gray">{formatDuration(print.print_time_seconds)}</span>
              )}
              {print.filament_used_grams != null && (
                <span className="text-xs text-bambu-gray">{Math.round(print.filament_used_grams)} g</span>
              )}
              {when && <span className="text-xs text-bambu-gray">{new Date(when).toLocaleString()}</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
