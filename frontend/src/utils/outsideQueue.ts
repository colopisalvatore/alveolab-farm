import type { OutsideQueuePrint, PrintQueueItem } from '../api/client';

/** Live status of one printer, as the queue page keeps it. */
export interface QueuePrinterStatus {
  progress?: number;
  remaining_time?: number;
  state?: string;
  subtask_name?: string;
  current_archive_id?: number;
}

/** A print a printer is running that no queue row owns: started from the
 *  slicer, the touchscreen or another tool. */
export interface LiveOutsidePrint {
  printerId: number;
  name: string;
  state: string;
  progress?: number;
  /** Minutes, as the printer reports it. */
  remainingMinutes?: number;
  archiveId?: number;
}

// Printer states that mean a job is on the plate (Bambu vocabulary; the
// provider layer maps other brands onto it).
export const BUSY_STATES = new Set(['RUNNING', 'PAUSE', 'PREPARE']);

/** Prints the printers report that no `printing` queue row accounts for. */
export function liveOutsidePrints(
  queueItems: PrintQueueItem[],
  statuses: Record<number, QueuePrinterStatus>,
): LiveOutsidePrint[] {
  const ownedByQueue = new Set(
    queueItems.filter((i) => i.status === 'printing' && i.printer_id != null).map((i) => i.printer_id),
  );
  const result: LiveOutsidePrint[] = [];
  for (const [id, status] of Object.entries(statuses)) {
    const printerId = Number(id);
    if (ownedByQueue.has(printerId) || !status.state || !BUSY_STATES.has(status.state)) continue;
    result.push({
      printerId,
      name: status.subtask_name || '',
      state: status.state,
      progress: status.progress,
      remainingMinutes: status.remaining_time,
      archiveId: status.current_archive_id,
    });
  }
  return result;
}

/** Archive end statuses, in the queue's vocabulary: the printer reports a
 *  stop as "aborted", the queue calls it cancelled. */
const HISTORY_STATUS: Record<string, PrintQueueItem['status']> = {
  completed: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
  aborted: 'cancelled',
};

/** Shape a finished outside-queue print like a queue history row, so it sorts
 *  and renders with the rest of the history. The id is negative so it can
 *  never collide with a queue row; `isOutsideQueueRow` recognises it. */
export function outsidePrintAsHistoryRow(print: OutsideQueuePrint): PrintQueueItem {
  return {
    id: -print.archive_id,
    printer_id: print.printer_id,
    printer_name: print.printer_name,
    archive_id: print.archive_id,
    archive_name: print.name,
    archive_thumbnail: print.has_thumbnail ? 'archive' : null,
    library_file_id: null,
    library_file_name: null,
    library_file_thumbnail: null,
    status: HISTORY_STATUS[print.status] ?? 'failed',
    started_at: print.started_at,
    completed_at: print.completed_at,
    created_at: print.created_at,
    print_time_seconds: print.print_time_seconds,
    filament_used_grams: print.filament_used_grams,
    filament_type: print.filament_type,
    filament_color: print.filament_color,
    error_message: null,
    batch_id: null,
    batch_name: null,
    created_by_id: null,
    created_by_username: null,
    target_model: null,
    target_location: null,
  } as unknown as PrintQueueItem;
}

export function isOutsideQueueRow(item: PrintQueueItem): boolean {
  return item.id < 0;
}
