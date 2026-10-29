import { useTranslation } from 'react-i18next';
import type { PrinterProviderId } from '../../api/client';

/** Manufacturer switch at the top of the add-printer dialog. */
export function ProviderPicker({
  value,
  onChange,
  disabled,
}: {
  value: PrinterProviderId;
  onChange: (provider: PrinterProviderId) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const options: Array<{ id: PrinterProviderId; label: string }> = [
    { id: 'bambu', label: t('farm.providerBambu') },
    { id: 'elegoo', label: t('farm.providerElegoo') },
  ];
  return (
    <div className="mb-4">
      <span className="block text-sm text-bambu-gray mb-1">{t('farm.provider')}</span>
      <div role="radiogroup" aria-label={t('farm.provider')} className="grid grid-cols-2 gap-2">
        {options.map((option) => (
          <button
            key={option.id}
            type="button"
            role="radio"
            aria-checked={value === option.id}
            disabled={disabled}
            onClick={() => onChange(option.id)}
            className={`px-3 py-2 rounded-lg text-sm font-medium border transition-colors ${
              value === option.id
                ? 'bg-bambu-green text-white border-bambu-green'
                : 'bg-bambu-dark text-bambu-gray border-bambu-dark-tertiary hover:text-white'
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Shown in place of the Bambu control row for a printer the farm only monitors. */
export function ProviderReadOnlyNotice() {
  const { t } = useTranslation();
  return (
    <p className="mt-3 rounded-lg bg-bambu-dark px-3 py-2 text-xs text-bambu-gray" data-testid="provider-read-only">
      {t('farm.readOnlyControls')}
    </p>
  );
}
