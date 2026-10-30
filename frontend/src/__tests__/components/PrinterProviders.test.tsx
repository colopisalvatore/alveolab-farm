/**
 * ALVEOLAB Farm provider layer on the frontend: capability gating and the
 * read-only CANVAS panel. Slot data mirrors what our real Centauri 2 reported
 * (fw 02.01.01.04): tray ids 0-3, one spool declared by hand on the touchscreen.
 */
import { describe, it, expect } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { render } from '../utils';
import { Capability, isBambuPrinter, providerAllows } from '../../components/printer-providers/capabilities';
import { CanvasPanel } from '../../components/printer-providers/CanvasPanel';
import { ProviderFaultsBadge } from '../../components/printer-providers/ProviderFaultsBadge';
import type { MultiColorSlot, PrinterCapabilities } from '../../api/client';

const elegooCaps: PrinterCapabilities = {
  provider: 'elegoo',
  model: 'Centauri 2',
  supported: ['multicolor_status', 'status', 'temperatures'],
  notes: { queue_dispatch: 'ELEGOO dispatch is M4' },
};

function slot(index: number, overrides: Partial<MultiColorSlot> = {}): MultiColorSlot {
  return {
    unit_id: 0,
    slot_index: index,
    loaded: false,
    material: null,
    color_hex: null,
    name: null,
    vendor: null,
    source: 'unknown',
    active: false,
    ...overrides,
  };
}

describe('providerAllows', () => {
  it('keeps every control on Bambu printers, including legacy rows without provider', () => {
    expect(isBambuPrinter({})).toBe(true);
    expect(providerAllows({ provider: 'bambu' }, undefined, Capability.QUEUE_DISPATCH)).toBe(true);
    expect(providerAllows({}, undefined, Capability.CAMERA)).toBe(true);
  });

  it('hides a control while ELEGOO capabilities are loading', () => {
    expect(providerAllows({ provider: 'elegoo' }, undefined, Capability.MULTICOLOR_STATUS)).toBe(false);
  });

  it('offers only what the backend declares for ELEGOO', () => {
    expect(providerAllows({ provider: 'elegoo' }, elegooCaps, Capability.MULTICOLOR_STATUS)).toBe(true);
    expect(providerAllows({ provider: 'elegoo' }, elegooCaps, Capability.QUEUE_DISPATCH)).toBe(false);
    expect(providerAllows({ provider: 'elegoo' }, elegooCaps, Capability.CAMERA)).toBe(false);
  });
});

describe('CanvasPanel', () => {
  it('shows the four trays 1-based, the active one and the empty ones', () => {
    const slots = [
      slot(0, { loaded: true, material: 'PLA', color_hex: '077747', name: 'PLA Matte', vendor: 'Generic', active: true }),
      slot(1),
      slot(2),
      slot(3),
    ];
    render(<CanvasPanel slots={slots} />);

    const loaded = screen.getByTestId('canvas-slot-0-0');
    expect(loaded).toHaveTextContent('1');
    expect(loaded).toHaveTextContent('PLA');
    expect(loaded.className).toContain('ring-bambu-green');
    expect(loaded).toHaveAttribute('title', 'Generic · PLA Matte · #077747 · In use');

    const empty = screen.getByTestId('canvas-slot-0-3');
    expect(empty).toHaveTextContent('4');
    expect(empty).toHaveTextContent('Empty');
    expect(empty.className).not.toContain('ring-bambu-green');

    // The CC2 cannot tell RFID from a hand-typed spool: say so.
    expect(screen.getByText('Colours and materials as set on the printer, not verified.')).toBeInTheDocument();
  });

  it('does not invent trays before the printer reports them', () => {
    render(<CanvasPanel slots={undefined} />);
    expect(screen.getByText('The printer has not reported its CANVAS yet.')).toBeInTheDocument();
    expect(screen.queryByTestId('canvas-slot-0-0')).toBeNull();
  });

  it('drops the "not verified" note when every loaded spool was detected', () => {
    render(<CanvasPanel slots={[slot(0, { loaded: true, material: 'PLA', source: 'detected' }), slot(1)]} />);
    expect(screen.queryByText('Colours and materials as set on the printer, not verified.')).toBeNull();
  });
});

describe('CanvasPanel commands (M5)', () => {
  const catalogue = {
    brands: ['ELEGOO', 'Generic'],
    filaments: [
      { name: 'PLA', type: 'PLA', code: '0x0000', min_temp: 190, max_temp: 230, elegoo: true, generic: true },
      { name: 'PLA Carbon', type: 'PLA', code: '0x0005', min_temp: 190, max_temp: 230, elegoo: false, generic: true },
      { name: 'PETG', type: 'PETG', code: '0x0100', min_temp: 230, max_temp: 260, elegoo: true, generic: true },
    ],
  };
  const slots = [
    slot(0, { loaded: true, material: 'PLA', color_hex: 'FFFFFF', name: 'PLA', vendor: 'Generic' }),
    slot(1, { loaded: true }), // spool present, no filament data (as our CC2 reported it)
  ];
  const all = new Set<string>([
    Capability.MULTICOLOR_SLOT_EDIT,
    Capability.MULTICOLOR_LOAD_UNLOAD,
    Capability.MULTICOLOR_AUTO_REFILL,
  ]);
  const canAll = (capability: string) => all.has(capability);

  function renderPanel(props: Partial<Parameters<typeof CanvasPanel>[0]> = {}) {
    return render(
      <CanvasPanel
        slots={slots}
        printerId={5}
        autoRefill={false}
        jobState="idle"
        connected
        can={canAll}
        canControl
        {...props}
      />,
    );
  }

  it('stays read-only when no CANVAS command is switched on', () => {
    renderPanel({ can: () => false });
    expect(screen.getByTestId('canvas-slot-0-0').tagName).toBe('DIV');
    expect(screen.queryByTestId('canvas-auto-refill')).toBeNull();
  });

  it('saves a slot with the filament, brand and colour chosen', async () => {
    let body: unknown = null;
    server.use(
      http.get('/api/v1/printer-providers/elegoo/canvas-filaments', () => HttpResponse.json(catalogue)),
      http.put('/api/v1/printer-providers/printers/5/canvas/0/slots/1', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ success: true });
      }),
    );
    renderPanel();
    fireEvent.click(screen.getByTestId('canvas-slot-0-1'));
    const select = await screen.findByTestId('canvas-filament-select');
    await waitFor(() => expect(select).toHaveValue('PLA'));
    fireEvent.change(select, { target: { value: 'PETG' } });
    fireEvent.click(screen.getByRole('button', { name: '#2850DF' }));
    fireEvent.click(screen.getByTestId('canvas-slot-save'));
    await waitFor(() => expect(body).toEqual({ filament_name: 'PETG', color: '#2850DF', brand: 'Generic' }));
  });

  it('offers under ELEGOO only ELEGOO filaments', async () => {
    server.use(http.get('/api/v1/printer-providers/elegoo/canvas-filaments', () => HttpResponse.json(catalogue)));
    renderPanel();
    fireEvent.click(screen.getByTestId('canvas-slot-0-0'));
    const select = await screen.findByTestId('canvas-filament-select');
    await waitFor(() => expect(select).toHaveTextContent('PLA Carbon'));
    fireEvent.change(screen.getByDisplayValue('Generic'), { target: { value: 'ELEGOO' } });
    expect(select).not.toHaveTextContent('PLA Carbon');
  });

  it('edits only when idle; loads idle or paused and only a known filament', async () => {
    server.use(http.get('/api/v1/printer-providers/elegoo/canvas-filaments', () => HttpResponse.json(catalogue)));
    renderPanel({ jobState: 'paused' });
    fireEvent.click(screen.getByTestId('canvas-slot-0-1'));
    await screen.findByTestId('canvas-filament-select');
    expect(screen.getByTestId('canvas-slot-save')).toBeDisabled();
    expect(screen.getByTestId('canvas-slot-load')).toBeDisabled(); // slot 2 has no filament set
    expect(screen.getByTestId('canvas-slot-unload')).toBeEnabled();
  });

  it('loads a slot and waits for the printer', async () => {
    let loaded = false;
    server.use(
      http.post('/api/v1/printer-providers/printers/5/canvas/0/slots/0/load', () => {
        loaded = true;
        return HttpResponse.json({ success: true });
      }),
    );
    renderPanel({ can: (c) => c === Capability.MULTICOLOR_LOAD_UNLOAD });
    fireEvent.click(screen.getByTestId('canvas-slot-0-0'));
    expect(screen.queryByTestId('canvas-filament-select')).toBeNull();
    fireEvent.click(screen.getByTestId('canvas-slot-load'));
    await waitFor(() => expect(loaded).toBe(true));
    expect(await screen.findByText('Slot 1 loaded')).toBeInTheDocument();
  });

  it('toggles auto-refill, and not while the printer has not reported it', async () => {
    let sent: unknown = null;
    server.use(
      http.put('/api/v1/printer-providers/printers/5/canvas/auto-refill', async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ success: true });
      }),
    );
    const { unmount } = renderPanel({ autoRefill: null });
    expect(screen.getByTestId('canvas-auto-refill')).toBeDisabled();
    unmount();
    renderPanel({ autoRefill: false });
    const toggle = screen.getByTestId('canvas-auto-refill');
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(toggle);
    await waitFor(() => expect(sent).toEqual({ enabled: true }));
  });

  it('keeps every command disabled without the control permission', async () => {
    renderPanel({ canControl: false, can: (c) => c !== Capability.MULTICOLOR_SLOT_EDIT });
    expect(screen.getByTestId('canvas-auto-refill')).toBeDisabled();
    fireEvent.click(screen.getByTestId('canvas-slot-0-0'));
    expect(screen.getByTestId('canvas-slot-load')).toBeDisabled();
    expect(screen.getByTestId('canvas-slot-unload')).toBeDisabled();
  });
});

describe('ProviderFaultsBadge', () => {
  it('shows nothing before the printer has reported anything', () => {
    render(<ProviderFaultsBadge faults={undefined} />);
    expect(screen.queryByTestId('provider-faults')).toBeNull();
  });

  it('counts the reported faults and lists them in the tooltip', () => {
    render(
      <ProviderFaultsBadge
        faults={[
          { code: '109', severity: 'error', message: 'filament runout' },
          { code: 'emergency_stop', severity: 'error', message: null },
        ]}
      />,
    );
    const badge = screen.getByTestId('provider-faults');
    expect(badge).toHaveTextContent('2');
    expect(badge.className).toContain('status-error');
    expect(badge.getAttribute('title')).toBe(
      ['Errors reported by the printer:', '109: filament runout', 'emergency_stop'].join('\n'),
    );
  });
});
