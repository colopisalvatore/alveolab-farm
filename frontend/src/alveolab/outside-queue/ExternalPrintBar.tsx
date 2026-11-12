import { useTranslation } from 'react-i18next';
import type { ExternalBar } from './outsideQueue';

function formatTooltipTime(date: Date): string {
  return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

/** Dashed, read-only bar of a print a printer runs outside the queue (no row to edit or stop). */
export function ExternalPrintBar({
  bar,
  rangeStartMs,
  rangeEndMs,
  minBarPx,
  barHeightPx,
}: {
  bar: ExternalBar;
  rangeStartMs: number;
  rangeEndMs: number;
  minBarPx: number;
  barHeightPx: number;
}) {
  const { t } = useTranslation();
  const rangeMs = rangeEndMs - rangeStartMs;
  const startMs = Math.max(rangeStartMs, bar.estimatedStart.getTime());
  const endMs = Math.min(rangeEndMs, bar.estimatedEnd.getTime());
  if (endMs <= rangeStartMs || startMs >= rangeEndMs) return null;
  const leftPct = ((startMs - rangeStartMs) / rangeMs) * 100;
  const widthPct = ((endMs - startMs) / rangeMs) * 100;
  const label = t('farm.outsideQueue.label');
  const tooltip = [
    bar.name || label,
    `${formatTooltipTime(bar.estimatedStart)} → ${formatTooltipTime(bar.estimatedEnd)}`,
    bar.progress != null ? `${Math.round(bar.progress)}%` : null,
    bar.name ? label : null,
  ]
    .filter(Boolean)
    .join(' · ');
  return (
    <div
      data-testid="timeline-external-print"
      title={tooltip}
      className="absolute rounded-md overflow-hidden flex items-center gap-1.5 px-1.5 bg-purple-500/20 border border-dashed border-purple-400/60"
      style={{ left: `${leftPct}%`, width: `max(${minBarPx}px, ${widthPct}%)`, top: 8, height: barHeightPx }}
    >
      <div className="min-w-0 flex-1">
        <div className="text-xs text-white font-medium truncate leading-tight">{bar.name || label}</div>
        <div className="text-[10px] text-bambu-gray truncate leading-tight">
          {label}
          {bar.progress != null ? ` · ${Math.round(bar.progress)}%` : ''}
        </div>
      </div>
      {bar.progress != null && (
        <div className="absolute bottom-0 left-0 h-0.5 bg-purple-300" style={{ width: `${bar.progress}%` }} aria-hidden />
      )}
    </div>
  );
}
