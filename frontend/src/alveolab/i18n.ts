// ALVEOLAB Farm strings live here, not in the Bambuddy locale files. Languages without
// a file fall back to English (i18next fallbackLng), so only the ones we use are kept.
import type { ResourceLanguage } from 'i18next';
import en from './locales/en';
import it from './locales/it';

const OURS: Record<string, ResourceLanguage> = { en, it };

/** The locale's resource with our keys added; the locale file is returned as it is for other languages. */
export function withFarmStrings(language: string, resource: ResourceLanguage): ResourceLanguage {
  const ours = OURS[language];
  return ours ? { ...resource, ...ours } : resource;
}
