"""CPU-only acceptance example: distinct test files become explicit jobs."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from dreamlake_eval import manifest, write_aggregate
from dreamlake_eval.model import atomic_write

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True, type=Path)
parser.add_argument("--dash-links", type=Path, help="Optional explicit per-test existing Dash URLs; never creates runs")
args = parser.parse_args()
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=False)
source = Path(__file__).parent
link_args = ["--dash-links", str(args.dash_links.resolve())] if args.dash_links else []
jobs = {"accuracy": "test_accuracy.py", "async": "test_async.py"}
suites, reports, codes = {}, [], []
for job, file in jobs.items():
    suite_path = output / f"{job}-suite.json"
    subprocess.run([sys.executable, "-m", "dreamlake_eval.cli", "discover", "--output", str(suite_path),
                    "--suite-id", "example", "--", file, *link_args], cwd=source, check=True)
    suites[job] = json.loads(suite_path.read_text())
# Declare every expected job before execution or accepting its results.
plan = manifest("cpu-example", suites)
atomic_write(output / "manifest.json", plan)
for job, file in jobs.items():
    attempt = output / job / "attempt-1"
    result = subprocess.run([sys.executable, "-m", "pytest", file, "-q", "--eval-suite-id", "example",
                             "--eval-output", str(attempt), "--eval-run-id", "cpu-example",
                             "--eval-job-id", job, "--eval-attempt-id", f"{job}-1", *link_args], cwd=source)
    codes.append(result.returncode)
    if (attempt / "eval.json").exists():
        reports.append(attempt / "eval.json")
result = write_aggregate(plan, reports, output / "aggregate", metrics=["reward", "durationSeconds"])
print(f"Aggregate complete: {result['complete']}; rows: {len(result['rows'])}")
raise SystemExit(max(codes) if result["complete"] else 2)
