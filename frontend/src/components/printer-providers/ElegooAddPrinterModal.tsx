/**
 * Add an ELEGOO Centauri Carbon 2 / Centauri 2 to the farm.
 *
 * Discovery is a read-only UDP query (backend POST /printer-providers/elegoo/discover):
 * unicast when an IP is typed — broadcast does not always reach the printer —
 * otherwise a broadcast on the farm's LAN. The backend probes the printer on
 * save and refuses to add it if the connection fails.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Search, AlertTriangle } from 'lucide-react';
import { api } from '../../api/client';
import type { ElegooDiscoveredPrinter, PrinterCreate, PrinterProviderId } from '../../api/client';
import { Button } from '../Button';
import { Card, CardContent } from '../Card';
import { ProviderPicker } from './ProviderPicker';

const IPV4 = /^(\d{1,3})(\.\d{1,3}){3}$/;
const inputClass =
  'w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none';

export function ElegooAddPrinterModal({
  onClose,
  onAdd,
  onProviderChange,
  existingSerials,
}: {
  onClose: () => void;
  onAdd: (data: PrinterCreate) => void;
  onProviderChange: (provider: PrinterProviderId) => void;
  existingSerials: string[];
}) {
  const { t } = useTranslation();
  const [form, setForm] = useState({
    name: '',
    ip_address: '',
    serial_number: '',
    access_code: '',
    model: '',
    location: '',
  });
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [found, setFound] = useState<ElegooDiscoveredPrinter[]>([]);
  const [lanOff, setLanOff] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const search = async () => {
    setError('');
    setSearching(true);
    setFound([]);
    const ip = form.ip_address.trim();
    try {
      const result = await api.discoverElegooPrinters(IPV4.test(ip) ? [ip] : []);
      setFound(result.filter((p) => !existingSerials.includes(p.serial_number)));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSearching(false);
      setSearched(true);
    }
  };

  const select = (printer: ElegooDiscoveredPrinter) => {
    setForm({
      ...form,
      // host_name comes padded on our CC2 ("Centauri  2").
      name: form.name || (printer.name ?? '').replace(/\s+/g, ' ').trim(),
      ip_address: printer.ip_address,
      serial_number: printer.serial_number,
      model: printer.model ?? '',
    });
    setLanOff(!printer.lan_only);
    setFound([]);
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    onAdd({
      name: form.name.trim(),
      ip_address: form.ip_address.trim(),
      serial_number: form.serial_number.trim(),
      access_code: form.access_code,
      model: form.model || undefined,
      location: form.location || undefined,
      // The archive pipeline reads Bambu 3MF over FTPS; ELEGOO archiving is M4.
      auto_archive: false,
      provider: 'elegoo',
    });
  };

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-start sm:items-center justify-center z-50 p-4 overflow-y-auto"
      onClick={onClose}
    >
      <Card className="w-full max-w-md my-auto max-h-[calc(100vh-2rem)] overflow-y-auto" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
        <CardContent>
          <h2 className="text-xl font-semibold mb-4">{t('printers.addPrinter')}</h2>
          <ProviderPicker value="elegoo" onChange={onProviderChange} />

          <form onSubmit={submit} className="space-y-4">
            <div>
              <label htmlFor="elegoo-ip" className="block text-sm text-bambu-gray mb-1">{t('printers.ipAddress')}</label>
              <div className="flex gap-2">
                <input
                  id="elegoo-ip"
                  type="text"
                  required
                  className={inputClass}
                  value={form.ip_address}
                  onChange={(e) => setForm({ ...form, ip_address: e.target.value })}
                  placeholder="192.168.1.75"
                />
                <Button type="button" variant="secondary" onClick={search} disabled={searching} className="flex-shrink-0">
                  {searching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                  {t('farm.elegooFind')}
                </Button>
              </div>
              <p className="mt-1 text-xs text-bambu-gray">{t('farm.elegooFindHint')}</p>
              {error && <p className="mt-1 text-sm text-red-700 dark:text-red-400">{error}</p>}
              {searched && !searching && !error && found.length === 0 && !form.serial_number && (
                <p className="mt-1 text-sm text-bambu-gray">{t('farm.elegooNotFound')}</p>
              )}
              {found.length > 0 && (
                <div className="mt-2 space-y-2">
                  {found.map((printer) => (
                    <button
                      key={printer.serial_number}
                      type="button"
                      onClick={() => select(printer)}
                      className="w-full text-left p-2 bg-bambu-dark rounded-lg hover:bg-bambu-dark-secondary transition-colors"
                    >
                      <p className="font-medium text-white text-sm truncate">{printer.model ?? printer.serial_number}</p>
                      <p className="text-xs text-bambu-gray truncate">{printer.ip_address} • {printer.serial_number}</p>
                    </button>
                  ))}
                </div>
              )}
              {lanOff && (
                <div className="mt-2 flex items-start gap-2 rounded-lg bg-amber-50 dark:bg-amber-500/10 border border-amber-300 dark:border-amber-500/30 p-2">
                  <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0 text-amber-600 dark:text-amber-400" />
                  <p className="text-sm text-amber-700 dark:text-amber-300">{t('farm.elegooLanOff')}</p>
                </div>
              )}
            </div>
            <div>
              <label htmlFor="elegoo-name" className="block text-sm text-bambu-gray mb-1">{t('printers.name')}</label>
              <input
                id="elegoo-name"
                type="text"
                required
                className={inputClass}
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder={t('printers.modal.myPrinter')}
              />
            </div>
            <div>
              <label htmlFor="elegoo-serial" className="block text-sm text-bambu-gray mb-1">{t('printers.serialNumber')}</label>
              <input
                id="elegoo-serial"
                type="text"
                required
                className={inputClass}
                value={form.serial_number}
                onChange={(e) => setForm({ ...form, serial_number: e.target.value })}
              />
            </div>
            <div>
              <label htmlFor="elegoo-code" className="block text-sm text-bambu-gray mb-1">{t('farm.accessCodeOptional')}</label>
              <input
                id="elegoo-code"
                type="password"
                autoComplete="off"
                maxLength={20}
                className={inputClass}
                value={form.access_code}
                onChange={(e) => setForm({ ...form, access_code: e.target.value })}
              />
            </div>
            <div>
              <label htmlFor="elegoo-location" className="block text-sm text-bambu-gray mb-1">{t('printers.modal.locationGroup')}</label>
              <input
                id="elegoo-location"
                type="text"
                className={inputClass}
                value={form.location}
                onChange={(e) => setForm({ ...form, location: e.target.value })}
                placeholder={t('printers.modal.locationPlaceholder')}
              />
            </div>
            <div className="flex gap-3 pt-2">
              <Button type="button" variant="secondary" onClick={onClose} className="flex-1">
                {t('common.cancel')}
              </Button>
              <Button type="submit" className="flex-1">
                {t('printers.addPrinter')}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
