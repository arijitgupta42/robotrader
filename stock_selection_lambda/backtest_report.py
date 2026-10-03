"""
Run the backtest report from the stored Universe Snapshots.

    python stock_selection_lambda/backtest_report.py --bucket BUCKET [--region eu-west-1] [--html report.html]

Reads  snapshots/YYYY-Www/universe.csv  for every week, prints the report, and optionally writes an HTML copy.
Only S3 reads.  (The analysis itself is lse_stock_analysis/backtest.py; the quarterly Lambda uses
`load_snapshots` and `build_report` from here too.)
"""
import argparse
import io
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lse_stock_analysis.backtest import build_report, render_html, render_text  # noqa: E402

SNAPSHOT_KEY = re.compile(r"^snapshots/(\d{4}-W\d{2})/universe\.csv$")


def load_snapshots(s3, bucket: str) -> dict[str, pd.DataFrame]:
    """Every weekly Universe Snapshot in the bucket, keyed by ISO week label, oldest first."""
    keys = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="snapshots/")
            for o in page.get("Contents", [])]
    out = {}
    for key in sorted(keys):
        m = SNAPSHOT_KEY.match(key)
        if m:
            out[m.group(1)] = pd.read_csv(io.BytesIO(s3.get_object(Bucket=bucket, Key=key)["Body"].read()))
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", default="eu-west-1")
    parser.add_argument("--html", type=Path, help="also write the report as HTML to this file")
    args = parser.parse_args(argv)

    import boto3
    snapshots = load_snapshots(boto3.client("s3", region_name=args.region), args.bucket)
    if not snapshots:
        print("No snapshots found.")
        return 1
    report = build_report(snapshots)
    print(render_text(report))
    if args.html:
        args.html.write_text(render_html(report), encoding="utf-8")
        print(f"\nHTML written to {args.html}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
