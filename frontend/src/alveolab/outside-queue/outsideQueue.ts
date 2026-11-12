import type { PrintQueueItem } from '../../api/client';

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

/** The same print as a timeline bar. */
export interface ExternalBar {
  printerId: number;
  name: string;
  estimatedStart: Date;
  estimatedEnd: Date;
  progress?: number;
}

const HOUR_MS = 60 * 60 * 1000;

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

export function toExternalBar(live: LiveOutsidePrint, nowMs: number): ExternalBar {
  const remainingMs = (live.remainingMinutes ?? 0) * 60 * 1000;
  const progress = live.progress;
  // Elapsed time is not reported; derive it from progress vs remaining,
  // and start the bar at now when that ratio is meaningless.
  const elapsedMs =
    progress != null && progress > 0 && progress < 100 && remainingMs > 0
      ? (remainingMs * progress) / (100 - progress)
      : 0;
  return {
    printerId: live.printerId,
    name: live.name,
    estimatedStart: new Date(nowMs - elapsedMs),
    estimatedEnd: new Date(nowMs + (remainingMs > 0 ? remainingMs : HOUR_MS)),
    progress,
  };
}

/** A print started outside the queue keeps the printer busy just the same:
 *  queued items for that printer wait for it to end. */
export function seedExternalChains(
  bars: ExternalBar[],
  lanesWithActive: Set<string>,
  chainEndByLane: Map<string, number>,
  nowMs: number,
): void {
  for (const bar of bars) {
    const lk = `printer:${bar.printerId}`;
    lanesWithActive.add(lk);
    chainEndByLane.set(lk, Math.max(chainEndByLane.get(lk) ?? nowMs, bar.estimatedEnd.getTime()));
  }
}

export function latestExternalEnd(bars: ExternalBar[]): number {
  return bars.reduce((latest, bar) => Math.max(latest, bar.estimatedEnd.getTime()), 0);
}
