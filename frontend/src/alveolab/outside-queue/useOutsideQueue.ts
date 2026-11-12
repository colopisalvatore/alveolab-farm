import { useMemo } from 'react';
import { useQueries } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { Printer, PrintQueueItem } from '../../api/client';
import { liveOutsidePrints, toExternalBar } from './outsideQueue';
import type { ExternalBar, LiveOutsidePrint, QueuePrinterStatus } from './outsideQueue';

interface Filters {
  printerId?: number | null;
  location?: string;
  /** Queue status filter; live prints only show for "" or "printing". */
  status?: string;
}

/**
 * Prints a printer runs that no queue row owns, from the live printer status.
 * Reads every printer's status (same query key as the cards, so nothing extra
 * is fetched when the Printers page is open).
 */
export function useOutsideQueue(
  queue: PrintQueueItem[] | undefined,
  printers: Printer[] | undefined,
  { printerId, location, status }: Filters = {},
): { live: LiveOutsidePrint[]; bars: ExternalBar[] } {
  const list = useMemo(() => printers ?? [], [printers]);
  const queries = useQueries({
    queries: list.map((p) => ({
      queryKey: ['printerStatus', p.id],
      queryFn: () => api.getPrinterStatus(p.id),
      refetchInterval: 5000,
    })),
  });
  const statusData = queries.map((q) => q.data);

  const statusMap = useMemo(() => {
    const map: Record<number, QueuePrinterStatus> = {};
    list.forEach((p, i) => {
      const data = statusData[i];
      if (data) {
        map[p.id] = {
          progress: data.progress ?? undefined,
          remaining_time: data.remaining_time ?? undefined,
          state: data.state ?? undefined,
          subtask_name: data.subtask_name ?? undefined,
          current_archive_id: data.current_archive_id ?? undefined,
        };
      }
    });
    return map;
    // statusData is rebuilt every render; its entries are the real dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list, ...statusData]);

  const live = useMemo(() => {
    if (status && status !== 'printing') return [];
    return liveOutsidePrints(queue ?? [], statusMap).filter((print) => {
      if (printerId && print.printerId !== printerId) return false;
      if (location) return list.find((p) => p.id === print.printerId)?.location === location;
      return true;
    });
  }, [queue, statusMap, status, printerId, location, list]);

  const bars = useMemo(() => {
    const nowMs = Date.now();
    return live.map((l) => toExternalBar(l, nowMs));
  }, [live]);

  return { live, bars };
}
