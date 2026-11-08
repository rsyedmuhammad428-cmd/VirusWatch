-- 0011: Khyber Pakhtunkhwa province, district and sub-division geography rows (Phase 7).
--
-- Closes dataset-audit gap #2 (docs/DATASET_AUDIT_REPORT.md): "No Sindh/Karachi (or any
-- sub-national) geography rows." This is the first sub-national geography seeded, driven by a
-- real, well-documented source (the KP district-week IDSR panel, CC BY 4.0 -- see
-- data/provenance/manifest.json's kp_idsr_district_week_2023_2026 entry).
--
-- Modeling choice, stated plainly: the geographies.admin_level check allows
-- ('country', 'admin1', 'admin2', 'city', 'region', 'site'), with no literal "subdivision" level.
-- The 6 sub-divisions (SD Bannu, SD DI Khan, SD Kohat, SD Lakki, SD Peshawar, SD Tank) are
-- administrative units finer than their parent district, so they are seeded as admin_level
-- 'site' with parent_geography_id pointing at their parent district -- not because they are
-- physical sites, but because 'site' is the finest level this schema offers and the parent-child
-- relationship is what the analysis/surveillance layers actually rely on, not the level label.

insert into public.geographies (country_code, country_name, admin_level, region_name, parent_geography_id, code_system, source_geography_code)
select 'PK', 'Pakistan', 'admin1', 'Khyber Pakhtunkhwa', g.id, 'kp_idsr', 'KP'
  from public.geographies g
 where g.country_code = 'PK' and g.admin_level = 'country'
on conflict on constraint geographies_natural_key do nothing;

insert into public.geographies (country_code, country_name, admin_level, region_name, parent_geography_id, code_system, source_geography_code)
select 'PK', 'Pakistan', 'admin2', d.name, kp.id, 'kp_idsr_district_crosswalk', d.name
  from public.geographies kp,
       (values
         ('Abbottabad'),
         ('Bajaur'),
         ('Bannu'),
         ('Battagram'),
         ('Buner'),
         ('Charsadda'),
         ('Chitral Lower'),
         ('Chitral Upper'),
         ('D.I. Khan'),
         ('Dir Lower'),
         ('Dir Upper'),
         ('Hangu'),
         ('Haripur'),
         ('Karak'),
         ('Khyber'),
         ('Kohat'),
         ('Kohistan Lower'),
         ('Kohistan Upper'),
         ('Kolai Palas'),
         ('Lakki Marwat'),
         ('Lower & Central Kurram'),
         ('Malakand'),
         ('Mansehra'),
         ('Mardan'),
         ('Mohmand'),
         ('North Waziristan'),
         ('Nowshera'),
         ('Orakzai'),
         ('Peshawar'),
         ('Shangla'),
         ('South Waziristan (undivided)'),
         ('South Waziristan Lower'),
         ('South Waziristan Upper'),
         ('Swabi'),
         ('Swat'),
         ('Tank'),
         ('Tor Ghar'),
         ('Upper Kurram')
       ) as d(name)
 where kp.country_code = 'PK' and kp.admin_level = 'admin1' and kp.region_name = 'Khyber Pakhtunkhwa'
on conflict on constraint geographies_natural_key do nothing;

insert into public.geographies (country_code, country_name, admin_level, region_name, parent_geography_id, code_system, source_geography_code)
select 'PK', 'Pakistan', 'site', s.name, parent.id, 'kp_idsr_district_crosswalk', s.name
  from (values
         ('SD Bannu', 'Bannu'),
         ('SD DI Khan', 'D.I. Khan'),
         ('SD Kohat', 'Kohat'),
         ('SD Lakki', 'Lakki Marwat'),
         ('SD Peshawar', 'Peshawar'),
         ('SD Tank', 'Tank')
       ) as s(name, parent_name)
  join public.geographies parent
    on parent.country_code = 'PK' and parent.admin_level = 'admin2'
   and parent.region_name = s.parent_name
on conflict on constraint geographies_natural_key do nothing;

insert into public.data_sources (slug, name, publisher, source_url, source_type, update_cadence, enabled, notes)
values
  ('pk_kp_idsr_district_panel', 'KP district-week notifiable infectious disease surveillance panel',
   'National Institutes of Health, Pakistan (source bulletins); panel compiled by the Data in '
   'Brief article''s authors',
   'https://www.nih.org.pk/', 'file_download', null, false,
   'Pre-compiled CC BY 4.0 CSV panel (164 of 169 weeks, 2023 w16 - 2026 w28), not a live feed. '
   'Distinct from pk_nih_weekly_bulletin: this source is the KP-only Table 4 of the same '
   'underlying bulletins, already parsed into a long-format panel. See '
   'app/ingestion/connectors/kp_idsr_parser.py and data/provenance/manifest.json '
   '(kp_idsr_district_week_2023_2026). Kept disabled until a live-refresh method (new bulletins '
   'past week 28 2026) is established.')
on conflict (slug) do nothing;
