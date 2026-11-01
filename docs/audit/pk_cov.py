import collections
import sys

sys.path.insert(0, sys.argv[1])
from app.ingestion.connectors.flunet_parser import parse_flunet_csv

r = parse_flunet_csv(
    open(sys.argv[2], "rb").read(), countries=frozenset({"PAK"}), since_year=None, source_url="x"
)
by = collections.defaultdict(list)
for o in r.observations:
    if o.indicator == "influenza_positivity" and o.data_status == "reported":
        by[o.surveillance_stream].append(o)
for s, items in by.items():
    items.sort(key=lambda o: o.reporting_period_start)
    yrs = collections.Counter(o.reporting_period_start.year for o in items)
    print(
        s,
        len(items),
        items[0].reporting_period_start,
        items[-1].reporting_period_end,
        dict(sorted(yrs.items())),
    )
    print(
        "  latest 6:",
        [
            (str(o.reporting_period_start), float(o.value_numeric), o.denominator)
            for o in items[-6:]
        ],
    )
allyrs = collections.Counter(o.reporting_period_start.year for o in r.observations)
print("all years", dict(sorted(allyrs.items())))
