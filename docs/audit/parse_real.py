import collections
import sys

root, csvpath = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
from app.ingestion.connectors.flunet_parser import parse_flunet_csv  # noqa: E402

data = open(csvpath, "rb").read()
for countries in (
    frozenset({"PAK"}),
    frozenset({"USA"}),
    frozenset({"PAK", "USA", "IND", "BGD", "AFG", "IRN", "GBR", "CAN"}),
):
    r = parse_flunet_csv(data, countries=countries, since_year=None, source_url="file://real")
    print("\n== countries", sorted(countries))
    print(
        "rows_read",
        r.rows_read,
        "in_scope",
        r.rows_in_scope,
        "out",
        r.rows_out_of_scope,
        "rejected",
        r.rows_rejected,
        "dup_ident",
        r.rows_duplicate_identical,
        "latest",
        r.latest_period_end,
        dict(r.reject_reason_counts()),
    )
    c = collections.Counter(
        (o.country_code, o.indicator, o.surveillance_stream, o.data_status) for o in r.observations
    )
    for k, v in sorted(c.items(), key=str)[:30]:
        print(" ", k, v)
    fl = collections.Counter(f for o in r.observations for f in o.quality_flags)
    print("  flags", dict(fl))
    if len(countries) == 1:
        pk = [
            o
            for o in r.observations
            if o.indicator == "influenza_positivity" or "positiv" in o.indicator
        ]
        if pk:
            print(
                "  first/last positivity", pk[0].reporting_period_start, pk[-1].reporting_period_end
            )
