/**
 * CANVAS (ELEGOO multicolour unit) panel for the printer card.
 *
 * Shows exactly what the printer reports. The CC2 protocol does not say
 * whether a spool was read from an RFID tag or typed in on the touchscreen,
 * so colours are labelled as declared, not verified.
 *
 * ALVEOLAB Farm M5: with the matching capability switched on, a slot opens
 * CanvasSlotModal (declare the spool, load / unload) and the header gets the
 * auto-refill switch. Same commands as ELEGOO's device page in Orca Slicer;
 * without the capabilities the panel stays read-only.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { MultiColorSlot } from '../../api/client';
import { FilamentSlotCircle } from '../FilamentSlotCircle';
import { useToast } from '../../contexts/ToastContext';
import { Capability, type CapabilityId } from './capabilities';
import { CanvasSlotModal } from './CanvasSlotModal';

function slotTitle(slot: MultiColorSlot, inUse: string): string {
  const parts = [slot.vendor, slot.name ?? slot.material].filter(Boolean) as string[];
  if (slot.color_hex) parts.push(`#${slot.color_hex}`);
  if (slot.active) parts.push(inUse);
  return parts.join(' · ');
}

export function CanvasPanel({
  slots,
  printerId,
  autoRefill,
  jobState,
  connected = false,
  can,
  canControl = false,
}: {
  slots: MultiColorSlot[] | undefined;
  printerId?: number;
  autoRefill?: boolean | null;
  jobState?: string;
  connected?: boolean;
  can?: (capability: CapabilityId) => boolean;
  canControl?: boolean;
}) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [openSlot, setOpenSlot] = useState<MultiColorSlot | null>(null);

  const units = new Map<number, MultiColorSlot[]>();
  for (const slot of slots ?? []) {
    units.set(slot.unit_id, [...(units.get(slot.unit_id) ?? []), slot]);
  }
  const anyUnverified = (slots ?? []).some((s) => s.loaded && s.source !== 'detected');

  const commandsOn = printerId !== undefined && can !== undefined;
  const canEdit = commandsOn && can(Capability.MULTICOLOR_SLOT_EDIT);
  const canLoadUnload = commandsOn && can(Capability.MULTICOLOR_LOAD_UNLOAD);
  const canAutoRefill = commandsOn && can(Capability.MULTICOLOR_AUTO_REFILL);
  const slotsOpen = canEdit || canLoadUnload;

  const autoRefillMutation = useMutation({
    mutationFn: (enabled: boolean) => api.setCanvasAutoRefill(printerId as number, enabled),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['printerTelemetry', printerId] }),
    onError: (error: Error) => showToast(error.message, 'error'),
  });
  const autoRefillDisabled = !connected || !canControl || autoRefill == null || autoRefillMutation.isPending;
  const autoRefillTitle = !canControl
    ? t('printers.permission.noControl')
    : autoRefill == null
      ? t('farm.canvasAutoRefillUnknown')
      : t('farm.canvasAutoRefillHint');

  return (
    <div className="mt-3" data-testid="canvas-panel">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[length:var(--pc-t10,10px)] uppercase tracking-wider text-bambu-gray font-medium">
          {t('printers.filaments')}
        </span>
        <div className="flex-1 h-[2px] bg-bambu-dark-tertiary" />
        {canAutoRefill && (
          <button
            type="button"
            role="switch"
            aria-checked={autoRefill === true}
            data-testid="canvas-auto-refill"
            disabled={autoRefillDisabled}
            title={autoRefillTitle}
            onClick={() => autoRefillMutation.mutate(!autoRefill)}
            className="flex items-center gap-1.5 text-[length:var(--pc-t10,10px)] text-bambu-gray disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {t('farm.canvasAutoRefill')}
            <span
              className={`relative inline-flex h-4 w-7 shrink-0 rounded-full transition-colors ${
                autoRefill ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
              }`}
            >
              <span
                className={`absolute top-0.5 h-3 w-3 rounded-full bg-white transition-transform ${
                  autoRefill ? 'translate-x-3.5' : 'translate-x-0.5'
                }`}
              />
            </span>
          </button>
        )}
      </div>

      {units.size === 0 ? (
        <p className="text-xs text-bambu-gray">{t('farm.canvasNotReported')}</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {[...units.entries()].map(([unitId, unitSlots]) => (
            <div key={unitId} className="min-w-0 flex-1 p-2 bg-bambu-dark rounded-[10px] space-y-1">
              <div className="flex w-full min-h-7 items-center rounded-lg bg-bambu-dark-secondary px-2 py-1">
                <span className="text-[length:var(--pc-t10,10px)] font-bold text-white">
                  {t('farm.canvas')}
                  {units.size > 1 ? ` ${unitId + 1}` : ''}
                </span>
              </div>
              <div className="grid gap-1" style={{ gridTemplateColumns: `repeat(${Math.max(unitSlots.length, 1)}, minmax(3.5rem, 1fr))` }}>
                {[...unitSlots]
                  .sort((a, b) => a.slot_index - b.slot_index)
                  .map((slot) => {
                    const isEmpty = slot.loaded === false;
                    const className = `relative w-full bg-bambu-dark-secondary rounded-lg p-1 text-center ${isEmpty ? 'opacity-50' : ''} ${
                      slot.active ? 'ring-2 ring-bambu-green ring-offset-1 ring-offset-bambu-dark' : ''
                    }`;
                    const content = (
                      <>
                        {/* tray_id is 0-based on the CC2; shown 1-based like the touchscreen. */}
                        <FilamentSlotCircle
                          trayColor={slot.color_hex}
                          trayType={slot.material}
                          isEmpty={isEmpty}
                          emptyKind={isEmpty ? 'physical' : null}
                          slotNumber={slot.slot_index + 1}
                        />
                        <div className="text-[length:var(--pc-t9,9px)] text-white font-bold truncate">
                          {isEmpty ? t('ams.slotEmpty') : slot.material || '?'}
                        </div>
                      </>
                    );
                    const testId = `canvas-slot-${slot.unit_id}-${slot.slot_index}`;
                    const title = isEmpty ? undefined : slotTitle(slot, t('farm.canvasInUse'));
                    return slotsOpen ? (
                      <button
                        key={slot.slot_index}
                        type="button"
                        data-testid={testId}
                        title={title ?? t('farm.canvasSlotOpen')}
                        onClick={() => setOpenSlot(slot)}
                        className={`${className} hover:bg-bambu-dark-tertiary transition-colors`}
                      >
                        {content}
                      </button>
                    ) : (
                      <div key={slot.slot_index} data-testid={testId} title={title} className={className}>
                        {content}
                      </div>
                    );
                  })}
              </div>
            </div>
          ))}
        </div>
      )}

      {anyUnverified && (
        <p className="mt-1 text-[length:var(--pc-t9,9px)] text-bambu-gray">{t('farm.canvasDeclared')}</p>
      )}

      {openSlot && printerId !== undefined && (
        <CanvasSlotModal
          printerId={printerId}
          slot={openSlot}
          jobState={jobState}
          connected={connected}
          canEdit={canEdit}
          canLoadUnload={canLoadUnload}
          canControl={canControl}
          onClose={() => setOpenSlot(null)}
        />
      )}
    </div>
  );
}
