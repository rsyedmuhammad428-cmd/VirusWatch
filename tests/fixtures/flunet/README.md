# FluNet test fixtures: SYNTHETIC, NOT REAL

`SYNTHETIC_FIXTURE_not_real_who_data.csv` was written by hand for the parser tests. **None of its numbers
come from WHO.** Its column subset is the set the parser reads, in the layout we believe the VIW_FNT export
uses; it is *not* a copy of a real download. Each row exercises one rule:

| line | purpose |
|---|---|
| 2 | normal sentinel row: all pathogens, Victoria sum, specimens != tested (flagged) |
| 3 | same week, NONSENTINEL stream: must be a separate series |
| 4 | every value blank: must become `missing`, never 0 |
| 5 | zero tests: positivity undefined, counts are real zeros |
| 6 | USA, small denominator flag |
| 7 | India: out of scope when not requested (not validated) |
| 8 | 2023 row: excluded by `since_year=2024` |
| 9 | negative count: rejected |
| 10 | unknown ORIGIN_SOURCE: rejected |
| 11 | ISO week 53 in a 52-week year: rejected |
| 12-13 | identical duplicate: kept once |
| 14-15 | conflicting duplicate: both rejected |
| 16 | ISO_WEEKSTARTDATE disagrees with the ISO week: rejected |
| 17 | INF_A + INF_B != INF_ALL: flagged, kept |
| 18 | 2020-W53 (a real 53-week year), NOTDEFINED stream |
| 19 | short row: rejected as malformed |

Never load these files outside tests. The database test harness additionally uses `example.test` URLs.
