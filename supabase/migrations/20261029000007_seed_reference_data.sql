-- 0007: reference metadata only. NO surveillance observations are seeded.
--
-- Every source starts DISABLED with unverified cadence/licence. A connector is enabled only after
-- its real endpoint/format has been verified (see README, "Source status"). Country rows carry
-- no population: population values are loaded later from a cited source, never guessed.

insert into public.data_sources (slug, name, publisher, source_url, source_type, update_cadence, enabled, notes)
values
  ('who_flunet', 'WHO FluNet', 'World Health Organization',
   'https://www.who.int/tools/flunet', 'other', null, false,
   'Country-level influenza laboratory surveillance. Access method and cadence to be verified in Phase 3. No sub-national (e.g. Karachi) resolution assumed.'),
  ('pk_nih_weekly_bulletin', 'Pakistan NIH Weekly Public Health Bulletin',
   'National Institute of Health, Pakistan',
   'https://www.nih.org.pk/phb/weekly-bulletin', 'pdf_bulletin', null, false,
   'Format/stability unverified. Table extraction requires human review. Geographic level mapped only where the document states it.'),
  ('cdc_fluview', 'CDC FluView', 'U.S. Centers for Disease Control and Prevention',
   'https://www.cdc.gov/fluview/', 'other', null, false,
   'US influenza surveillance. Download/API method to be verified in Phase 7.'),
  ('cdc_nwss', 'CDC National Wastewater Surveillance System', 'U.S. Centers for Disease Control and Prevention',
   'https://www.cdc.gov/nwss/wastewater-surveillance.html', 'file_download', null, false,
   'Wastewater concentrations are not comparable to clinical case counts. Download method to be verified in Phase 7.')
on conflict (slug) do nothing;

insert into public.geographies (country_code, country_name, admin_level, code_system, source_geography_code)
values
  ('PK', 'Pakistan',      'country', 'ISO3166-1-alpha2', 'PK'),
  ('US', 'United States', 'country', 'ISO3166-1-alpha2', 'US')
on conflict on constraint geographies_natural_key do nothing;
