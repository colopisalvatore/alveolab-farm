/**
 * Job controls of a non-Bambu printer card (ALVEOLAB Farm, M4).
 *
 * Each button exists only when the backend declares its capability, which a
 * deployment switches on after the matching hardware test. With none of them
 * the card keeps the read-only notice. Pause / resume / stop go through the
 * same confirmation dialogs as the Bambu card.
 */
import { useTranslation } from 'react-i18next';
import { Pause, Play, Printer as PrinterIcon, Square } from 'lucide-react';
import { Capability, type CapabilityId } from './capabilities';
import { ProviderReadOnlyNotice } from './ProviderPicker';

const buttonClass =
  'flex h-8 items-center justify-center gap-1 px-3 rounded-lg text-xs font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed';

export function ProviderJobControls({
  state,
  connected,
  can,
  canControl,
  canQueue,
  busy,
  onPause,
  onResume,
  onStop,
  onPrint,
}: {
  state: string | null | undefined;
  connected: boolean;
  can: (capability: CapabilityId) => boolean;
  canControl: boolean;
  canQueue: boolean;
  busy: boolean;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  onPrint: () => void;
}) {
  const { t } = useTranslation();
  const canPauseResume = can(Capability.JOB_PAUSE_RESUME);
  const canStop = can(Capability.JOB_STOP);
  const canPrint = can(Capability.QUEUE_DISPATCH);
  if (!canPauseResume && !canStop && !canPrint) return <ProviderReadOnlyNotice />;

  const isRunning = state === 'RUNNING';
  const isPaused = state === 'PAUSE';
  const noControl = !connected || busy || !canControl;
  const noControlTitle = !canControl ? t('printers.permission.noControl') : undefined;

  return (
    <div className="mt-3" data-testid="provider-job-controls">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[length:var(--pc-t10,10px)] uppercase tracking-wider text-bambu-gray font-medium">
          {t('printers.controls')}
        </span>
        <div className="flex-1 h-[2px] bg-bambu-dark-tertiary" />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {canPrint && (
          <button
            type="button"
            onClick={onPrint}
            disabled={!canQueue}
            title={!canQueue ? t('fileManager.noPermissionAddToQueue') : undefined}
            className={`${buttonClass} bg-bambu-green text-white hover:bg-bambu-green-dark`}
          >
            <PrinterIcon className="w-3.5 h-3.5" />
            {t('farm.printFile')}
          </button>
        )}
        {canPauseResume && isRunning && (
          <button
            type="button"
            onClick={onPause}
            disabled={noControl}
            title={noControlTitle}
            className={`${buttonClass} bg-bambu-dark text-white hover:bg-bambu-dark-tertiary`}
          >
            <Pause className="w-3.5 h-3.5" />
            {t('printers.pause')}
          </button>
        )}
        {canPauseResume && isPaused && (
          <button
            type="button"
            onClick={onResume}
            disabled={noControl}
            title={noControlTitle}
            className={`${buttonClass} bg-bambu-dark text-white hover:bg-bambu-dark-tertiary`}
          >
            <Play className="w-3.5 h-3.5" />
            {t('printers.resume')}
          </button>
        )}
        {canStop && (isRunning || isPaused) && (
          <button
            type="button"
            onClick={onStop}
            disabled={noControl}
            title={noControlTitle}
            className={`${buttonClass} bg-red-500/15 text-red-600 dark:text-red-400 hover:bg-red-500/25`}
          >
            <Square className="w-3.5 h-3.5" />
            {t('printers.stop')}
          </button>
        )}
      </div>
    </div>
  );
}
