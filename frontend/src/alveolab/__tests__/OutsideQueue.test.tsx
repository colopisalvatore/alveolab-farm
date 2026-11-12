/**
 * ALVEOLAB Farm: prints a printer runs (or ran) without a queue row.
 * A farm prints from the slicer as much as from the queue; without this the
 * Queue page showed nothing printing and the timeline showed idle lanes.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render as plainRender, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../../__tests__/mocks/server';
import { render } from '../../__tests__/utils';
import { QueuePage } from '../../pages/QueuePage';
import { QueueTimelineView } from '../../components/QueueTimelineView';
import { liveOutsidePrints, toExternalBar } from '../outside-queue/outsideQueue';
import type { PrintQueueItem, Printer } from '../../api/client';

const HOUR = 3600;
const farm = [
  { id: 1, name: 'Workshop X1C', model: 'X1C' },
  { id: 2, name: 'Centauri 2', model: 'Centauri 2' },
] as unknown as Printer[];

const pending = (id: number, name: string, printerId: number) =>
  ({
    id,
    printer_id: printerId,
    status: 'pending',
    position: id,
    archive_name: name,
    print_time_seconds: HOUR,
    scheduled_time: null,
    manual_start: false,
    waiting_reason: null,
  }) as unknown as PrintQueueItem;

describe('liveOutsidePrints', () => {
  it('skips idle printers and printers the queue is printing on', () => {
    const queue = [{ id: 1, printer_id: 1, status: 'printing' } as unknown as PrintQueueItem];
    const live = liveOutsidePrints(queue, {
      1: { state: 'RUNNING' },
      2: { state: 'RUNNING', progress: 78, remaining_time: 69, subtask_name: 'Kawasaki.gcode' },
      3: { state: 'IDLE' },
    });
    expect(live).toHaveLength(1);
    expect(live[0]).toMatchObject({ printerId: 2, name: 'Kawasaki.gcode', progress: 78 });
  });
});

describe('QueueTimelineView and prints started outside the queue', () => {
  function renderFarm(items: PrintQueueItem[], statuses: Record<number, Record<string, unknown>>) {
    const bars = liveOutsidePrints(items, statuses).map((l) => toExternalBar(l, Date.now()));
    plainRender(
      <QueueTimelineView
        queueItems={items}
        printers={farm}
        printerStatuses={statuses}
        externalBars={bars}
        sjfEnabled={false}
        onItemClick={() => {}}
        t={(key: string) => key}
      />,
    );
  }

  it('draws a read-only bar for a print the printer runs on its own', () => {
    renderFarm([], {
      1: { state: 'IDLE' },
      2: { state: 'RUNNING', progress: 78, remaining_time: 69, subtask_name: 'Kawasaki.gcode' },
    });
    const bars = screen.getAllByTestId('timeline-external-print');
    expect(bars).toHaveLength(1);
    expect(bars[0].getAttribute('title')).toMatch(/^Kawasaki\.gcode · .* · 78%/);
    expect(screen.queryByRole('button', { name: /Kawasaki/ })).toBeNull();
    expect(screen.queryByText('queue.timeline.nothingCommitted')).toBeNull();
  });

  it('chains queued items behind it', () => {
    renderFarm([pending(1, 'Next job', 2)], {
      2: { state: 'RUNNING', progress: 50, remaining_time: 120, subtask_name: 'From Orca' },
    });
    const next = screen.getAllByRole('button').find((el) => el.getAttribute('title')?.startsWith('Next job'));
    const external = screen.getByTestId('timeline-external-print');
    expect(next).toBeDefined();
    expect(parseFloat(next!.style.left)).toBeGreaterThan(parseFloat(external.style.left));
  });
});

describe('QueuePage and prints started outside the queue', () => {
  const printer = { id: 1, name: 'P1S-2', model: 'P1S', enabled: true, created_at: '2024-01-01T00:00:00Z' };

  beforeEach(() => {
    vi.mocked(localStorage.getItem).mockImplementation(() => null);
    server.use(
      http.get('/api/v1/queue/', () => HttpResponse.json([])),
      http.get('/api/v1/printers/', () => HttpResponse.json([printer])),
      http.get('/api/v1/printers/:id/status', () =>
        HttpResponse.json({
          id: 1,
          name: 'P1S-2',
          connected: true,
          state: 'RUNNING',
          progress: 21,
          remaining_time: 488,
          subtask_name: 'Knitted Snowman',
          current_archive_id: null,
        }),
      ),
      http.get('/api/v1/alveolab/queue/outside-prints', () =>
        HttpResponse.json([
          {
            archive_id: 351,
            printer_id: 1,
            printer_name: 'P1S-2',
            name: 'Knitted Reindeer',
            status: 'aborted',
            started_at: '2026-10-30T07:41:46Z',
            completed_at: '2026-10-30T09:00:00Z',
            created_at: '2026-10-30T07:41:46Z',
            print_time_seconds: 4200,
            filament_used_grams: 30,
            filament_type: 'PLA',
            filament_color: '#FFFFFF',
            failure_reason: null,
            has_thumbnail: false,
          },
        ]),
      ),
    );
  });

  it('lists a print the printer runs on its own under Currently Printing', async () => {
    render(<QueuePage />);
    const card = await screen.findByTestId('outside-queue-print');
    expect(within(card).getByText('Knitted Snowman')).toBeInTheDocument();
    expect(within(card).getByText('Outside the queue')).toBeInTheDocument();
    expect(within(card).getByText('21%')).toBeInTheDocument();
    expect(within(card).queryByRole('button')).toBeNull();
  });

  it('lists finished prints from the archive under the history', async () => {
    const user = userEvent.setup();
    render(<QueuePage />);
    await user.click(await screen.findByRole('button', { name: /History/ }));
    const section = await screen.findByTestId('outside-queue-history');
    expect(within(section).getByText('Knitted Reindeer')).toBeInTheDocument();
  });
});
