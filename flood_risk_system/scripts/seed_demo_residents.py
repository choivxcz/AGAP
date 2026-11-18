"""Load demo households into the MDRRMO resident registry (for testing and demonstrations).

Households come from the project dataset (Activities/AGAP_NOAH_Dataset/agap_noah_dataset.csv): their locations and
household profiles. They are SIMULATED households, so they are registered under the name "Demo household <ID>",
without contact numbers, and flagged is_demo=1. Remove them with --clear before entering real residents.

    python scripts/seed_demo_residents.py            # 300 households, spread over all barangays
    python scripts/seed_demo_residents.py --n 1000
    python scripts/seed_demo_residents.py --clear    # delete all demo households
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from app.ops import api, store  # noqa: E402

DATASET = ROOT.parent / "Activities" / "AGAP_NOAH_Dataset" / "agap_noah_dataset.csv"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=300, help="number of demo households (default 300)")
    parser.add_argument("--clear", action="store_true", help="delete all demo households and exit")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    with store.connect() as conn:
        if args.clear:
            n = conn.execute("DELETE FROM resident WHERE is_demo = 1").rowcount
            conn.commit()
            print(f"Deleted {n} demo households.")
            return
        existing = {r["full_name"] for r in store.list_residents(conn)}

    api.seed_facilities()
    df = pd.read_csv(DATASET, keep_default_na=False).drop_duplicates("HouseholdID")
    # Spread over barangays: the same share from each, then fill up at random.
    per = max(1, args.n // df.Barangay.nunique())
    sample = df.groupby("Barangay", group_keys=False).apply(
        lambda g: g.sample(min(len(g), per), random_state=args.seed))
    if len(sample) < args.n:
        sample = pd.concat([sample, df.drop(sample.index).sample(args.n - len(sample), random_state=args.seed)])
    sample = sample.head(args.n)

    added = skipped = 0
    with store.connect() as conn:
        for i, row in enumerate(sample.itertuples(index=False), 1):
            name = f"Demo household {row.HouseholdID}"
            if name in existing:
                skipped += 1
                continue
            req = api.ResidentIn(full_name=name, address_text=f"{row.Barangay} (simulated)",
                                 latitude=float(row.Latitude), longitude=float(row.Longitude),
                                 location_source="survey", age=int(row.Age), housing_material=row.HousingMaterial,
                                 house_members=int(row.HouseMembers), children_count=int(row.ChildrenCount),
                                 elder_count=int(row.ElderCount), pwd_count=int(row.PWDCount),
                                 notes="Simulated household from the AGAP NOAH dataset")
            try:
                api._register(conn, req, is_demo=True)
                added += 1
            except Exception as exc:  # e.g. a point on water: skip, report
                print(f"  skipped {row.HouseholdID}: {getattr(exc, 'detail', exc)}")
                skipped += 1
            if i % 50 == 0:
                print(f"  {i}/{len(sample)}")
        n = api.repredict(conn)
    print(f"Added {added} demo households ({skipped} skipped). {n} households re-predicted under the active scenario.")


if __name__ == "__main__":
    main()
