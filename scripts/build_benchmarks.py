"""Pre-analyses companies so benchmarks are instant on the live site.

Usage (from the repository root, with GEMINI_API_KEY in backend/.env):
    python -m scripts.build_benchmarks infosys tcs wipro hcltech techm
    python -m scripts.build_benchmarks --group "IT services"

Results go to data/benchmarks.json. Commit that file and redeploy.
"""
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / "backend" / ".env")

from backend import benchmark, peers  # noqa: E402
from backend.company_search import load_catalog  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="*", help="company ids from companies.json")
    ap.add_argument("--group", help='industry group, e.g. "IT services"')
    ap.add_argument("--force", action="store_true", help="re-analyse companies already in the seed file")
    args = ap.parse_args()

    cat = load_catalog()
    chosen = [c for c in cat if c["id"] in args.ids or (args.group and benchmark.industry_of(c) == args.group)]
    if not chosen:
        print("No matching companies. Groups:", ", ".join(sorted({benchmark.industry_of(c) for c in cat})))
        return 1
    seed = peers._read(peers.SEED)
    for c in chosen:
        if c["id"] in seed and not args.force:
            print(f"skip   {c['name']} (already in seed)")
            continue
        print(f"start  {c['name']}")
        try:
            peers.analyse_company(c, on_msg=lambda m: print("       " + m))
            rec = peers._read(peers.RUNTIME)[c["id"]]
            peers.put(c, {"f": rec["findings"], "meta": rec["meta"]}, rec["source_url"], path=peers.SEED)
            print(f"done   {c['name']}")
        except Exception as e:  # noqa: BLE001
            print(f"FAILED {c['name']}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
