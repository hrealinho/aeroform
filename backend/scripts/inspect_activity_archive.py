#!/usr/bin/env python3
from __future__ import annotations
from collections import Counter
from pathlib import Path
import argparse

from app.importers.formats import activity_format
from app.importers.zip_import import safe_members


def main():
    parser = argparse.ArgumentParser(description="Inspect a FIT/GPX/TCX ZIP or Strava activities archive without importing it.")
    parser.add_argument("archive")
    args = parser.parse_args()
    path = Path(args.archive)
    members = list(safe_members(str(path)))
    counts = Counter()
    compressed = 0
    for member in members:
        fmt = activity_format(member.filename)
        if not fmt:
            continue
        ext, gz = fmt
        counts[ext + (".gz" if gz else "")] += 1
        compressed += int(gz)
    print(f"Archive: {path}")
    print(f"Supported activities: {len(members)}")
    print(f"Gzip-compressed activities: {compressed}")
    for kind, count in sorted(counts.items()):
        print(f"  {kind}: {count}")


if __name__ == "__main__":
    main()
