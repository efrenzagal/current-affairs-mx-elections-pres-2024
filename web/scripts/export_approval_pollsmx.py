"""Export the latest locally ingested PollsMX runs for future website use."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from approval.pollsmx import export_web

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "election_data.db")
    parser.add_argument("--output", type=Path, default=ROOT / "web/public/data/approval-pollsmx.json")
    args = parser.parse_args()
    export_web(args.db, args.output)
