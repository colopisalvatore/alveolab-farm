/**
 * ALVEOLAB Farm: an ELEGOO printer on the printers page.
 *
 * The farm only monitors the Centauri 2 so far, so its card must not offer
 * any Bambu control that would fail or, worse, send a Bambu flow to it:
 * no Print button, camera, file manager, control row, HMS badge or MQTT
 * debug. The Bambu card next to it must keep all of them.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { render } from '../utils';
import { PrintersPage, AddPrinterModal } from '../../pages/PrintersPage';

const bambu = {
  id: 1,
  name: 'P1S-1',
  ip_address: '192.168.1.100',
  serial_number: '01P00A000000001',
  access_code: '12345678',
  model: 'P1S',
  provider: 'bambu',
  location: null,
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const elegoo = {
  ...bambu,
  id: 5,
  name: 'Centauri 2',
  ip_address: '192.168.1.75',
  serial_number: 'CC2SERIAL0001',
  model: 'Centauri 2',
  provider: 'elegoo',
  auto_archive: false,
};

const idleStatus = {
  connected: true,
  state: 'IDLE',
  progress: 0,
  layer_num: 0,
  total_layers: 0,
  temperatures: { nozzle: 27, bed: 22 },
  remaining_time: 0,
  filename: null,
  vt_tray: [],
  ams: [],
};

const capabilitiesRequests: number[] = [];
let telemetryFaults: Array<{ code: string; severity: string; message: string | null }> = [];
// M4 job capabilities a deployment has switched on for the CC2.
let jobCaps: string[] = [];
let elegooState = 'IDLE';

beforeEach(() => {
  capabilitiesRequests.length = 0;
  telemetryFaults = [];
  jobCaps = [];
  elegooState = 'IDLE';
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([bambu, elegoo])),
    http.get('/api/v1/printers/:id/status', ({ params }) =>
      HttpResponse.json(Number(params.id) === 5 ? { ...idleStatus, state: elegooState } : idleStatus),
    ),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/printer-providers/printers/:id/capabilities', ({ params }) => {
      capabilitiesRequests.push(Number(params.id));
      return HttpResponse.json({
        provider: 'elegoo',
        model: 'Centauri 2',
        supported: ['auto_reconnect', 'fan_speeds', 'multicolor_status', 'status', 'temperatures', ...jobCaps],
        notes: { queue_dispatch: 'ELEGOO dispatch is M4' },
      });
    }),
    http.get('/api/v1/printer-providers/printers/:id/telemetry', () =>
      HttpResponse.json({
        provider: 'elegoo',
        connection: 'online',
        job_state: 'idle',
        firmware_version: '02.01.01.04',
        faults: telemetryFaults,
        multicolor_slots: [0, 1, 2, 3].map((i) => ({
          unit_id: 0,
          slot_index: i,
          loaded: i === 0,
          material: i === 0 ? 'PLA' : null,
          color_hex: i === 0 ? '077747' : null,
          name: i === 0 ? 'PLA Matte' : null,
          vendor: i === 0 ? 'Generic' : null,
          source: 'unknown',
          active: false,
        })),
      }),
    ),
  );
});

async function cards() {
  await waitFor(() => {
    expect(document.getElementById('printer-card-1')).not.toBeNull();
    expect(document.getElementById('printer-card-5')).not.toBeNull();
  });
  return {
    bambuCard: document.getElementById('printer-card-1') as HTMLElement,
    elegooCard: document.getElementById('printer-card-5') as HTMLElement,
  };
}

describe('PrintersPage — ELEGOO card', () => {
  it('shows the CANVAS and a read-only notice instead of Bambu controls', async () => {
    render(<PrintersPage />);
    const { elegooCard } = await cards();

    await waitFor(() => expect(within(elegooCard).getByTestId('canvas-slot-0-0')).toBeInTheDocument());
    expect(within(elegooCard).getByTestId('provider-read-only')).toBeInTheDocument();
    expect(within(elegooCard).queryByText('Controls')).toBeNull();
    expect(within(elegooCard).queryByRole('button', { name: 'Print' })).toBeNull();
    expect(within(elegooCard).queryByTitle('Browse Files')).toBeNull();
    expect(within(elegooCard).queryByLabelText('Camera View Mode')).toBeNull();
    expect(within(elegooCard).queryByTitle(/HMS/i)).toBeNull();
  });

  it('keeps the Bambu card exactly as upstream and asks no capabilities for it', async () => {
    render(<PrintersPage />);
    const { bambuCard } = await cards();

    await waitFor(() => expect(within(bambuCard).getByText('Controls')).toBeInTheDocument());
    expect(within(bambuCard).getByRole('button', { name: 'Print' })).toBeInTheDocument();
    expect(within(bambuCard).getByLabelText('Camera View Mode')).toBeInTheDocument();
    expect(within(bambuCard).queryByTestId('provider-read-only')).toBeNull();
    await waitFor(() => expect(capabilitiesRequests).toContain(5));
    expect(capabilitiesRequests).not.toContain(1);
  });

  it('hides the Bambu-only entries of the card menu', async () => {
    render(<PrintersPage />);
    const { elegooCard } = await cards();

    // common.more is '+{{count}} more'; the kebab button reuses it as its title.
    fireEvent.click(within(elegooCard).getByTitle(/more$/i));
    expect(within(elegooCard).getByText('Reconnect')).toBeInTheDocument();
    expect(within(elegooCard).queryByText('MQTT Debug')).toBeNull();
  });
});

describe('PrintersPage — ELEGOO faults and bulk actions', () => {
  it('shows the faults the printer reports, with their code', async () => {
    telemetryFaults = [{ code: '109', severity: 'error', message: 'filament runout' }];
    render(<PrintersPage />);
    const { elegooCard, bambuCard } = await cards();

    const badge = await within(elegooCard).findByTestId('provider-faults');
    expect(badge).toHaveTextContent('1');
    expect(badge.getAttribute('title')).toContain('109: filament runout');
    expect(within(bambuCard).queryByTestId('provider-faults')).toBeNull();
  });

  it('shows OK when the printer reports no fault', async () => {
    render(<PrintersPage />);
    const { elegooCard } = await cards();
    const badge = await within(elegooCard).findByTestId('provider-faults');
    expect(badge).toHaveTextContent('OK');
    expect(badge).toHaveAttribute('title', 'No errors reported by the printer');
  });

  it('never sends a bulk Bambu command to the ELEGOO printer', async () => {
    const resumed: number[] = [];
    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json({ ...idleStatus, state: 'PAUSE' })),
      http.post('/api/v1/printers/:id/print/resume', ({ params }) => {
        resumed.push(Number(params.id));
        return HttpResponse.json({ success: true, message: 'ok' });
      }),
    );
    render(<PrintersPage />);
    await cards();

    fireEvent.click(screen.getByTitle('Select'));
    fireEvent.click(await screen.findByText('Select All'));
    await waitFor(() => expect(screen.getByText('2 selected')).toBeInTheDocument());
    // The bulk toolbar's Resume, not the paused Bambu card's own button.
    const toolbar = (await screen.findByText('2 selected')).closest('div.fixed') as HTMLElement;
    const resume = within(toolbar).getByRole('button', { name: 'Resume' });
    await waitFor(() => expect(resume).not.toBeDisabled());
    fireEvent.click(resume);

    await waitFor(() => expect(resumed).toEqual([1]));
    expect(await screen.findByText('Skipped printers of other brands (monitoring only): 1')).toBeInTheDocument();
  });
});

describe('AddPrinterModal — ELEGOO', () => {
  it('finds the printer by IP and adds it as an ELEGOO without auto-archive', async () => {
    const discoverBodies: unknown[] = [];
    server.use(
      http.post('/api/v1/printer-providers/elegoo/discover', async ({ request }) => {
        discoverBodies.push(await request.json());
        return HttpResponse.json([
          {
            ip_address: '192.168.1.75',
            serial_number: 'CC2SERIAL0002',
            name: 'Centauri  2',
            model: 'Centauri 2',
            access_code_required: true,
            lan_only: false,
            provider: 'elegoo',
          },
        ]);
      }),
    );
    const onAdd = vi.fn();
    render(<AddPrinterModal onClose={() => {}} onAdd={onAdd} existingSerials={[]} />);

    await userEvent.click(screen.getByRole('radio', { name: 'ELEGOO' }));
    await userEvent.type(screen.getByLabelText(/IP Address/), '192.168.1.75');
    await userEvent.click(screen.getByRole('button', { name: /find/i }));

    expect(await screen.findByText('192.168.1.75 • CC2SERIAL0002')).toBeInTheDocument();
    expect(discoverBodies).toEqual([{ targets: ['192.168.1.75'], timeout: 5 }]);
    await userEvent.click(screen.getByText('192.168.1.75 • CC2SERIAL0002'));

    // lan_status 0 on discovery: warn before the backend probe refuses it.
    expect(screen.getByText(/LAN Only Mode is off/)).toBeInTheDocument();
    expect(screen.getByLabelText('Name')).toHaveValue('Centauri 2');

    await userEvent.click(screen.getByRole('button', { name: 'Add Printer' }));
    expect(onAdd).toHaveBeenCalledWith(
      expect.objectContaining({
        provider: 'elegoo',
        serial_number: 'CC2SERIAL0002',
        ip_address: '192.168.1.75',
        model: 'Centauri 2',
        auto_archive: false,
      }),
    );
  });
});

describe('PrintersPage — ELEGOO job control (M4)', () => {
  const allJobCaps = ['file_upload', 'job_start', 'job_pause_resume', 'job_stop', 'queue_dispatch'];

  it('stays read-only until the deployment switches the commands on', async () => {
    elegooState = 'RUNNING';
    render(<PrintersPage />);
    const { elegooCard } = await cards();
    await waitFor(() => expect(within(elegooCard).getByTestId('provider-read-only')).toBeInTheDocument());
    expect(within(elegooCard).queryByTestId('provider-job-controls')).toBeNull();
    expect(within(elegooCard).queryByRole('button', { name: /pause/i })).toBeNull();
  });

  it('pauses a running print after confirmation', async () => {
    jobCaps = allJobCaps;
    elegooState = 'RUNNING';
    const paused: number[] = [];
    server.use(
      http.post('/api/v1/printers/:id/print/pause', ({ params }) => {
        paused.push(Number(params.id));
        return HttpResponse.json({ success: true, message: 'ok' });
      }),
    );
    render(<PrintersPage />);
    const { elegooCard } = await cards();
    const controls = await within(elegooCard).findByTestId('provider-job-controls');
    expect(within(controls).queryByRole('button', { name: 'Resume' })).toBeNull();
    expect(within(controls).getByRole('button', { name: 'Stop' })).toBeInTheDocument();
    fireEvent.click(within(controls).getByRole('button', { name: 'Pause' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Pause Print' }));
    await waitFor(() => expect(paused).toEqual([5]));
  });

  it('offers only files sliced for the Centauri 2 and queues the chosen one', async () => {
    jobCaps = allJobCaps;
    const queued: unknown[] = [];
    const file = (id: number, filename: string, sliced: string | null) => ({
      id,
      folder_id: null,
      is_external: false,
      filename,
      file_type: filename.endsWith('.gcode') ? 'gcode' : 'gcode.3mf',
      file_size: 1000,
      thumbnail_path: null,
      print_count: 0,
      duplicate_count: 0,
      created_by_id: null,
      created_by_username: null,
      created_at: '2026-10-30T00:00:00Z',
      fs_modified_at: null,
      print_name: null,
      print_time_seconds: null,
      filament_used_grams: null,
      sliced_for_model: sliced,
      tags: [],
    });
    server.use(
      http.get('/api/v1/library/files', () =>
        HttpResponse.json([
          file(10, 'benchy_cc2.gcode', 'Elegoo Centauri Carbon 2'),
          file(11, 'benchy_p1s.gcode.3mf', 'P1S'),
          file(12, 'mystery.gcode', null),
        ]),
      ),
      http.post('/api/v1/queue/', async ({ request }) => {
        queued.push(await request.json());
        return HttpResponse.json({ id: 99 });
      }),
    );
    render(<PrintersPage />);
    const { elegooCard } = await cards();
    fireEvent.click(await within(elegooCard).findByRole('button', { name: 'Print a file' }));

    const option = await screen.findByRole('radio', { name: 'benchy_cc2.gcode' });
    expect(screen.queryByRole('radio', { name: 'benchy_p1s.gcode.3mf' })).toBeNull();
    expect(screen.queryByRole('radio', { name: 'mystery.gcode' })).toBeNull();
    fireEvent.click(option);
    fireEvent.click(screen.getByLabelText('Level the bed before printing'));
    fireEvent.click(screen.getByRole('button', { name: 'Add to queue' }));

    await waitFor(() =>
      expect(queued).toEqual([{ printer_id: 5, library_file_id: 10, bed_levelling: 'on' }]),
    );
  });

  it('keeps the Bambu print button and drop flow for Bambu printers only', async () => {
    jobCaps = allJobCaps;
    render(<PrintersPage />);
    const { bambuCard, elegooCard } = await cards();
    await waitFor(() => expect(within(bambuCard).getByRole('button', { name: 'Print' })).toBeInTheDocument());
    await within(elegooCard).findByTestId('provider-job-controls');
    expect(within(elegooCard).queryByRole('button', { name: 'Print' })).toBeNull();
  });
});
