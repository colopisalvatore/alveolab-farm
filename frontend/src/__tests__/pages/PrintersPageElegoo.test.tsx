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

beforeEach(() => {
  capabilitiesRequests.length = 0;
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([bambu, elegoo])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(idleStatus)),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/printer-providers/printers/:id/capabilities', ({ params }) => {
      capabilitiesRequests.push(Number(params.id));
      return HttpResponse.json({
        provider: 'elegoo',
        model: 'Centauri 2',
        supported: ['auto_reconnect', 'fan_speeds', 'multicolor_status', 'status', 'temperatures'],
        notes: { queue_dispatch: 'ELEGOO dispatch is M4' },
      });
    }),
    http.get('/api/v1/printer-providers/printers/:id/telemetry', () =>
      HttpResponse.json({
        provider: 'elegoo',
        connection: 'online',
        job_state: 'idle',
        firmware_version: '02.01.01.04',
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
