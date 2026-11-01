# Dataset fixtures

Verbatim excerpts (no edited values) of the files supplied by the project owner, kept small:

| File | Taken from |
|---|---|
| covid_pk_2020_excerpt.csv | covid-19-cases-in-pakistan-2020.csv (OpenData Pakistan), rows for 9 dates incl. mixed date formats, the "No 0" cell, negative tests, repeated day |
| who_gho_excerpt.csv | health_indicators_pak.csv (WHO GHO export): ambiguous-key rows, a unique row, measles reported cases |
| nih_week26_table1.txt | `pdftotext -layout` of the NIH IDSR bulletin, week 26 of 2026: title line and Table 1 |
| facilities_excerpt.csv | first 12 rows of pakistan.csv (health facility points) |

The originals are never modified; their SHA-256 values are in `data/provenance/manifest.json`.
