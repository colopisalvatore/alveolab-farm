/**
 * One CANVAS slot of an ELEGOO Centauri 2 (ALVEOLAB Farm, M5): declare the
 * spool in it, load or unload its filament.
 *
 * Mirrors ELEGOO's device page in Orca Slicer: brand ELEGOO or Generic,
 * filament from ELEGOO's own table (the backend fills type, code and
 * temperatures), editing only while the printer is idle, load / unload when
 * idle or paused and loading only from a slot with a known filament. The
 * backend checks every rule again; a control the deployment has not switched
 * on is not shown at all.
 */
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowDownToLine, ArrowUpFromLine, Loader2 } from 'lucide-react';
import { api } from '../../api/client';
import type { CanvasFilament, MultiColorSlot } from '../../api/client';
import { Button } from '../Button';
import { Card, CardContent } from '../Card';
import { useToast } from '../../contexts/ToastContext';

// Same printer states as the backend's STARTABLE_STATES / LOAD_UNLOAD_STATES.
const IDLE_STATES = new Set(['idle', 'finished', 'cancelled', 'failed']);
const LOAD_STATES = new Set([...IDLE_STATES, 'paused']);

// The swatches of ELEGOO's own colour picker.
const SWATCHES = [
  '#FFFFFF', '#FFF242', '#DBF47A', '#09CC3A', '#077747', '#0B6283', '#0BE2A0', '#74D9F3',
  '#48A7FA', '#2850DF', '#433089', '#A03BF7', '#F32FF8', '#D4B1DD', '#F95D77', '#F72221',
  '#7C4C00', '#F88D36', '#FCEBD7', '#D2C5A3', '#AF7832', '#898989', '#BCBCBC', '#000000',
];

function offeredUnder(filament: CanvasFilament, brand: string): boolean {
  return brand === 'ELEGOO' ? filament.elegoo : filament.generic;
}

export function CanvasSlotModal({
  printerId,
  slot,
  jobState,
  connected,
  canEdit,
  canLoadUnload,
  canControl,
  onClose,
}: {
  printerId: number;
  slot: MultiColorSlot;
  jobState: string | undefined;
  connected: boolean;
  canEdit: boolean;
  canLoadUnload: boolean;
  canControl: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const { data: catalogue } = useQuery({
    queryKey: ['canvasFilaments'],
    queryFn: api.getCanvasFilaments,
    enabled: canEdit,
    staleTime: Infinity,
  });

  const [brand, setBrand] = useState(slot.vendor === 'ELEGOO' ? 'ELEGOO' : 'Generic');
  const [filamentName, setFilamentName] = useState<string | null>(null);
  const [color, setColor] = useState(slot.color_hex ? `#${slot.color_hex}` : '#FFFFFF');

  const offered = useMemo(
    () => (catalogue?.filaments ?? []).filter((f) => offeredUnder(f, brand)),
    [catalogue, brand],
  );
  // Start from what the slot holds; keep the choice valid when the brand changes.
  const selected =
    offered.find((f) => f.name === filamentName) ??
    offered.find((f) => f.name === slot.name) ??
    offered.find((f) => f.name === slot.material) ??
    offered[0];

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['printerTelemetry', printerId] });
  const saveMutation = useMutation({
    mutationFn: () =>
      api.setCanvasSlot(printerId, slot.unit_id, slot.slot_index, {
        filament_name: selected?.name ?? '',
        color,
        brand,
      }),
    onSuccess: () => {
      showToast(t('farm.canvasSlotSaved', { slot: slot.slot_index + 1 }));
      refresh();
      onClose();
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });
  const loadMutation = useMutation({
    mutationFn: (action: 'load' | 'unload') =>
      action === 'load'
        ? api.loadCanvasSlot(printerId, slot.unit_id, slot.slot_index)
        : api.unloadCanvasSlot(printerId, slot.unit_id, slot.slot_index),
    onSuccess: (_, action) => {
      showToast(t(action === 'load' ? 'farm.canvasLoaded' : 'farm.canvasUnloaded', { slot: slot.slot_index + 1 }));
      refresh();
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });
  const busy = saveMutation.isPending || loadMutation.isPending;

  const state = jobState ?? 'unknown';
  const editAllowed = connected && canControl && IDLE_STATES.has(state);
  const loadUnloadAllowed = connected && canControl && LOAD_STATES.has(state);
  const recognised = slot.loaded === true && !!slot.material && !!slot.color_hex;
  const noControl = !canControl ? t('printers.permission.noControl') : undefined;

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-start sm:items-center justify-center z-50 p-4 overflow-y-auto"
      onClick={onClose}
    >
      <Card
        className="w-full max-w-md my-auto max-h-[calc(100vh-2rem)] overflow-y-auto"
        onClick={(e: React.MouseEvent) => e.stopPropagation()}
        data-testid="canvas-slot-modal"
      >
        <CardContent>
          <h2 className="text-xl font-semibold mb-4">
            {t('farm.canvasSlotTitle', { slot: slot.slot_index + 1 })}
          </h2>

          {canEdit && (
            <section className="space-y-3">
              <div className="grid grid-cols-2 gap-2">
                <label className="text-xs text-bambu-gray">
                  {t('farm.canvasBrand')}
                  <select
                    value={brand}
                    onChange={(e) => setBrand(e.target.value)}
                    className="mt-1 w-full rounded-lg bg-bambu-dark border border-bambu-dark-tertiary px-2 py-1.5 text-sm text-white"
                  >
                    {(catalogue?.brands ?? ['ELEGOO', 'Generic']).map((b) => (
                      <option key={b} value={b}>
                        {b}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="text-xs text-bambu-gray">
                  {t('farm.canvasFilament')}
                  <select
                    value={selected?.name ?? ''}
                    onChange={(e) => setFilamentName(e.target.value)}
                    data-testid="canvas-filament-select"
                    className="mt-1 w-full rounded-lg bg-bambu-dark border border-bambu-dark-tertiary px-2 py-1.5 text-sm text-white"
                  >
                    {offered.map((f) => (
                      <option key={f.name} value={f.name}>
                        {f.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              {selected && (
                <p className="text-xs text-bambu-gray">
                  {t('farm.canvasNozzleRange', { min: selected.min_temp, max: selected.max_temp })}
                </p>
              )}

              <div>
                <span className="text-xs text-bambu-gray">{t('farm.canvasColor')}</span>
                <div className="mt-1 grid grid-cols-8 gap-1.5">
                  {SWATCHES.map((swatch) => (
                    <button
                      key={swatch}
                      type="button"
                      aria-label={swatch}
                      aria-pressed={color.toUpperCase() === swatch}
                      onClick={() => setColor(swatch)}
                      className={`h-6 rounded-md border ${
                        color.toUpperCase() === swatch ? 'ring-2 ring-bambu-green border-transparent' : 'border-bambu-dark-tertiary'
                      }`}
                      style={{ backgroundColor: swatch }}
                    />
                  ))}
                </div>
                <label className="mt-2 flex items-center gap-2 text-xs text-bambu-gray">
                  {t('farm.canvasCustomColor')}
                  <input
                    type="color"
                    value={color}
                    onChange={(e) => setColor(e.target.value.toUpperCase())}
                    className="h-7 w-10 rounded bg-transparent"
                  />
                  <span className="font-mono text-white">{color.toUpperCase()}</span>
                </label>
              </div>

              <div className="flex items-center justify-end gap-2">
                {!editAllowed && canControl && (
                  <span className="mr-auto text-xs text-bambu-gray">{t('farm.canvasEditIdleOnly')}</span>
                )}
                <Button
                  onClick={() => saveMutation.mutate()}
                  disabled={!editAllowed || !selected || busy}
                  title={noControl}
                  data-testid="canvas-slot-save"
                >
                  {saveMutation.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
                  {t('farm.canvasSaveSlot')}
                </Button>
              </div>
            </section>
          )}

          {canLoadUnload && (
            <section className={canEdit ? 'mt-5 pt-4 border-t border-bambu-dark-tertiary' : ''}>
              <p className="text-xs text-bambu-gray mb-2">{t('farm.canvasLoadHint')}</p>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="secondary"
                  onClick={() => loadMutation.mutate('load')}
                  disabled={!loadUnloadAllowed || !recognised || busy}
                  title={noControl ?? (!recognised ? t('farm.canvasLoadNeedsFilament') : undefined)}
                  data-testid="canvas-slot-load"
                >
                  {loadMutation.isPending && loadMutation.variables === 'load' ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <ArrowDownToLine className="w-4 h-4" />
                  )}
                  {t('farm.canvasLoad')}
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => loadMutation.mutate('unload')}
                  disabled={!loadUnloadAllowed || busy}
                  title={noControl}
                  data-testid="canvas-slot-unload"
                >
                  {loadMutation.isPending && loadMutation.variables === 'unload' ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <ArrowUpFromLine className="w-4 h-4" />
                  )}
                  {t('farm.canvasUnload')}
                </Button>
              </div>
              {!loadUnloadAllowed && canControl && (
                <p className="mt-2 text-xs text-bambu-gray">{t('farm.canvasLoadIdleOnly')}</p>
              )}
            </section>
          )}

          <div className="mt-5 flex justify-end">
            <Button variant="secondary" onClick={onClose}>
              {t('common.close')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
