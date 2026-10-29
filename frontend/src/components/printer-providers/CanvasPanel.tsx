/**
 * Read-only CANVAS (ELEGOO multicolour unit) panel for the printer card.
 *
 * Shows exactly what the printer reports. The CC2 protocol does not say
 * whether a spool was read from an RFID tag or typed in on the touchscreen,
 * so colours are labelled as declared, not verified. No slot editing here:
 * the protocol has no method for it (backend capability multicolor_slot_edit).
 */
import { useTranslation } from 'react-i18next';
import type { MultiColorSlot } from '../../api/client';
import { FilamentSlotCircle } from '../FilamentSlotCircle';

function slotTitle(slot: MultiColorSlot, inUse: string): string {
  const parts = [slot.vendor, slot.name ?? slot.material].filter(Boolean) as string[];
  if (slot.color_hex) parts.push(`#${slot.color_hex}`);
  if (slot.active) parts.push(inUse);
  return parts.join(' · ');
}

export function CanvasPanel({ slots }: { slots: MultiColorSlot[] | undefined }) {
  const { t } = useTranslation();
  const units = new Map<number, MultiColorSlot[]>();
  for (const slot of slots ?? []) {
    units.set(slot.unit_id, [...(units.get(slot.unit_id) ?? []), slot]);
  }
  const anyUnverified = (slots ?? []).some((s) => s.loaded && s.source !== 'detected');

  return (
    <div className="mt-3" data-testid="canvas-panel">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[length:var(--pc-t10,10px)] uppercase tracking-wider text-bambu-gray font-medium">
          {t('printers.filaments')}
        </span>
        <div className="flex-1 h-[2px] bg-bambu-dark-tertiary" />
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
                    return (
                      <div
                        key={slot.slot_index}
                        data-testid={`canvas-slot-${slot.unit_id}-${slot.slot_index}`}
                        title={isEmpty ? undefined : slotTitle(slot, t('farm.canvasInUse'))}
                        className={`relative w-full bg-bambu-dark-secondary rounded-lg p-1 text-center ${isEmpty ? 'opacity-50' : ''} ${
                          slot.active ? 'ring-2 ring-bambu-green ring-offset-1 ring-offset-bambu-dark' : ''
                        }`}
                      >
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
    </div>
  );
}
