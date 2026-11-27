"""Small explicit discovery/replay commands. Pytest owns collection semantics."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from .model import atomic_write, replay
from .aggregate import manifest, write_aggregate


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dreamlake-eval")
    commands = parser.add_subparsers(dest="command", required=True)
    discover = commands.add_parser("discover", help="Collect with pytest; imports/hooks may have side effects")
    discover.add_argument("--output", required=True)
    discover.add_argument("--suite-id", default="pytest")
    discover.add_argument("--source", help="Optional JSON provider/source provenance")
    discover.add_argument("--workflow", help="Explicit JSON workflow snapshot")
    discover.add_argument("--context", default="{}")
    discover.add_argument("pytest_args", nargs=argparse.REMAINDER)
    recover = commands.add_parser("replay", help="Rebuild report facts from a journal; never execute tests")
    recover.add_argument("journal")
    recover.add_argument("--output", required=True)
    recover.add_argument("--allow-truncated-tail", action="store_true")
    plan = commands.add_parser("manifest", help="Declare expected jobs from explicit suite JSON files")
    plan.add_argument("--evaluation-id", required=True)
    plan.add_argument("--job", action="append", required=True, metavar="ID=SUITE_JSON")
    plan.add_argument("--output", required=True)
    merge = commands.add_parser("aggregate", help="Merge explicit report files without inventing missing jobs or measurements")
    merge.add_argument("--manifest", required=True)
    merge.add_argument("--report", action="append", default=[])
    merge.add_argument("--output", required=True)
    merge.add_argument("--metric", action="append", default=[])
    capture = commands.add_parser("workflow", help="Capture explicit workflow source as validated JSON, never execute")
    capture.add_argument("source")
    capture.add_argument("--format", choices=["json", "yaml"], required=True)
    capture.add_argument("--provider", required=True)
    capture.add_argument("--revision", required=True)
    capture.add_argument("--path", required=True, help="Authored repository path for provenance")
    capture.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "workflow":
        from .workflow import workflow_snapshot, write_workflow
        try:
            snapshot = workflow_snapshot(Path(args.source).read_bytes().decode("utf-8"),
                                         format=args.format, provider=args.provider, revision=args.revision, path=args.path)
            write_workflow(args.output, snapshot)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        return 0
    if args.command == "manifest":
        jobs = {}
        for entry in args.job:
            key, sep, path = entry.partition("=")
            if not sep or key in jobs:
                parser.error("--job requires unique ID=SUITE_JSON entries")
            jobs[key] = json.loads(Path(path).read_text())
        atomic_write(Path(args.output), manifest(args.evaluation_id, jobs))
        return 0
    if args.command == "aggregate":
        write_aggregate(json.loads(Path(args.manifest).read_text()), args.report, args.output, metrics=args.metric)
        return 0
    if args.command == "discover":
        if importlib.util.find_spec("pytest") is None:
            parser.error("install dreamlake-eval[pytest] to use pytest discovery")
        rest = args.pytest_args[1:] if args.pytest_args[:1] == ["--"] else args.pytest_args
        if args.workflow:
            rest = ["--eval-workflow", str(Path(args.workflow).resolve()), *rest]
        if args.source:
            rest = ["--eval-source", args.source, *rest]
        # The installed pytest11 entry point loads our adapter exactly once.
        return subprocess.call([sys.executable, "-m", "pytest", "--collect-only", "-q",
                                "--eval-discovery", str(Path(args.output).resolve()),
                                "--eval-suite-id", args.suite_id, "--eval-context", args.context, *rest])
    atomic_write(Path(args.output), replay(args.journal, allow_truncated_tail=args.allow_truncated_tail))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
