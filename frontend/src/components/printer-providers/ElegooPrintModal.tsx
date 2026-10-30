/**
 * Queue a library file on an ELEGOO Centauri 2 (ALVEOLAB Farm, M4).
 *
 * Only files that say they were sliced for a Centauri 2 are offered; a Bambu
 * 3MF never reaches this list, and the backend checks the file again before
 * uploading it. The job goes through the normal queue, so it starts when the
 * printer is idle and its plate has been confirmed clear.
 */
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FileText, Loader2 } from 'lucide-react';
import { api } from '../../api/client';
import type { Printer } from '../../api/client';
import { Button } from '../Button';
import { Card, CardContent } from '../Card';
import { useToast } from '../../contexts/ToastContext';
import { isSlicedForCentauri2 } from './capabilities';

export function ElegooPrintModal({ printer, onClose }: { printer: Pick<Printer, 'id' | 'name'>; onClose: () => void }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [fileId, setFileId] = useState<number | null>(null);
  const [levelBed, setLevelBed] = useState(false);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // include_root=false without a folder lists every file of the library.
  const { data: files, isLoading } = useQuery({
    queryKey: ['libraryFiles', 'all-for-provider'],
    queryFn: () => api.getLibraryFiles(null, false),
  });
  const printable = useMemo(
    () => (files ?? []).filter((f) => isSlicedForCentauri2(f.sliced_for_model)),
    [files],
  );

  const queueMutation = useMutation({
    mutationFn: () =>
      api.addToQueue({
        printer_id: printer.id,
        library_file_id: fileId,
        bed_levelling: levelBed ? 'on' : 'off',
      }),
    onSuccess: () => {
      showToast(t('farm.printQueued', { name: printer.name }));
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      onClose();
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-start sm:items-center justify-center z-50 p-4 overflow-y-auto"
      onClick={onClose}
    >
      <Card
        className="w-full max-w-md my-auto max-h-[calc(100vh-2rem)] overflow-y-auto"
        onClick={(e: React.MouseEvent) => e.stopPropagation()}
      >
        <CardContent>
          <h2 className="text-xl font-semibold mb-1">{t('farm.printFileTitle', { name: printer.name })}</h2>
          <p className="text-xs text-bambu-gray mb-4">{t('farm.printFileHint')}</p>

          {isLoading ? (
            <div className="flex justify-center py-6">
              <Loader2 className="w-5 h-5 animate-spin text-bambu-gray" />
            </div>
          ) : printable.length === 0 ? (
            <p className="rounded-lg bg-bambu-dark px-3 py-3 text-sm text-bambu-gray" data-testid="elegoo-print-empty">
              {t('farm.printFileNone')}
            </p>
          ) : (
            <div role="radiogroup" aria-label={t('farm.printFile')} className="space-y-1 max-h-72 overflow-y-auto">
              {printable.map((file) => (
                <button
                  key={file.id}
                  type="button"
                  role="radio"
                  aria-checked={fileId === file.id}
                  onClick={() => setFileId(file.id)}
                  className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-left text-sm border transition-colors ${
                    fileId === file.id
                      ? 'border-bambu-green bg-bambu-green/10 text-white'
                      : 'border-bambu-dark-tertiary bg-bambu-dark text-bambu-gray hover:text-white'
                  }`}
                >
                  <FileText className="w-4 h-4 shrink-0" />
                  <span className="truncate">{file.print_name || file.filename}</span>
                </button>
              ))}
            </div>
          )}

          <label className="mt-4 flex items-center gap-2 text-sm text-bambu-gray">
            <input type="checkbox" checked={levelBed} onChange={(e) => setLevelBed(e.target.checked)} />
            {t('farm.levelBeforePrint')}
          </label>

          <div className="mt-5 flex justify-end gap-2">
            <Button variant="secondary" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button onClick={() => queueMutation.mutate()} disabled={fileId === null || queueMutation.isPending}>
              {queueMutation.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
              {t('farm.addToQueue')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
