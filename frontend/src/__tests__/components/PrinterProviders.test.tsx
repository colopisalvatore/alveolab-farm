/**
 * ALVEOLAB Farm provider layer on the frontend: capability gating and the
 * read-only CANVAS panel. Slot data mirrors what our real Centauri 2 reported
 * (fw 02.01.01.04): tray ids 0-3, one spool declared by hand on the touchscreen.
 */
import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
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
