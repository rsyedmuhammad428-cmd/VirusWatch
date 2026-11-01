# CDC FluView / NREVSS / ILINet: findings for the Phase 7 connector

Observed in the CSV files supplied by the project owner (not yet ingested; no connector exists).

- Files begin with a preamble line (title) before the header row; the parser must skip it explicitly.
- Weeks follow the MMWR calendar (Sunday-Saturday), not ISO weeks. Do not reuse the FluNet week logic.
- `X` marks a missing value. It is missing, not zero.
- The latest week is provisional and is revised later; store it as `provisional`.
- Files supplied are national-level only; state/region data needs separate downloads.
- Derived percentages in the files are consistent with the counts, but recompute and compare rather than trust.
- ILINet (outpatient illness) and NREVSS (laboratory positivity) measure different things and must not be merged
  into one series.
- Licence/terms not yet verified.
