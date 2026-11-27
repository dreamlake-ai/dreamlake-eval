"""Pytest is an adapter: preserve its inventory, fixture lifecycle and outcomes."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import pytest

from .model import Recorder, SCHEMA_VERSION, atomic_write, json_value


def pytest_addoption(parser):
    group = parser.getgroup("dreamlake-eval")
    group.addoption("--eval-output", help="New directory for this attempt's explicit reports/events")
    group.addoption("--eval-discovery", help="Write serializable collection inventory; use --collect-only")
    group.addoption("--eval-suite-id", default="pytest", help="Stable suite identity")
    group.addoption("--eval-run-id", help="Shared evaluation identity across jobs")
    group.addoption("--eval-job-id", default="local", help="Explicit job/shard identity within the evaluation")
    group.addoption("--eval-attempt-id", help="Unique attempt identity within the job")
    group.addoption("--eval-attempt-number", type=int, default=1, help="Explicit increasing attempt number; never wall-clock ordering")
    group.addoption("--eval-source", help="Optional JSON provider/source provenance; never executes a workflow")
    group.addoption("--eval-workflow", help="Explicit validated workflow.json snapshot; never raw YAML")
    group.addoption("--eval-context", default="{}", help="Explicit JSON attributes, e.g. machine/dataset/seed")
    group.addoption("--eval-resume-from", help="Prior eval.json; workload must explicitly restore its checkpoint")
    group.addoption("--eval-prior-stopped", action="store_true", help="Caller has reconciled prior execution as stopped")


def pytest_configure(config):
    if not config.getoption("eval_output") and not config.getoption("eval_discovery"):
        return
    if config.getoption("eval_discovery") and not config.getoption("collectonly"):
        raise pytest.UsageError("--eval-discovery requires --collect-only")
    if config.getoption("eval_output") and config.getoption("collectonly"):
        raise pytest.UsageError("use --eval-discovery for collection-only inventory")
    if config.getoption("eval_output") and (not config.getoption("eval_run_id") or not config.getoption("eval_attempt_id")):
        raise pytest.UsageError("--eval-output requires --eval-run-id and --eval-attempt-id")
    if getattr(config.option, "numprocesses", None):
        raise pytest.UsageError("one writer per evaluation job: use separate invocations/job IDs, not pytest-xdist")
    try:
        context = json_value(json.loads(config.getoption("eval_context")))
        if not isinstance(context, dict):
            raise ValueError()
    except (ValueError, TypeError):
        raise pytest.UsageError("--eval-context must be a finite JSON object") from None
    source = None
    if config.getoption("eval_source"):
        try:
            from .model import text
            source = json_value(json.loads(config.getoption("eval_source")))
            if not isinstance(source, dict):
                raise ValueError()
            text(source.get("provider"))
        except (ValueError, TypeError):
            raise pytest.UsageError("--eval-source requires a JSON object with provider") from None
    workflow = None
    if config.getoption("eval_workflow"):
        try:
            from .workflow import read_workflow
            workflow = read_workflow(config.getoption("eval_workflow"))
        except (ValueError, OSError) as exc:
            raise pytest.UsageError(str(exc)) from None
    config.pluginmanager.register(Adapter(config, context, source, workflow), "dreamlake-eval-recorder")


def _parameter(value):
    try:
        return json_value(value)
    except ValueError:
        # Never pickle or invoke repr/str on fixture/parameter objects.
        return {"serialized": False, "type": f"{type(value).__module__}.{type(value).__qualname__}"}


class Adapter:
    def __init__(self, config, context, source, workflow):
        self.config, self.context, self.source = config, context, source
        self.workflow = workflow
        self.items, self.errors = [], []
        self.recorder = None
        self.interrupted = False

    def pytest_itemcollected(self, item):
        self.items.append(item)

    def pytest_collectreport(self, report):
        if report.failed:
            self.errors.append({"nodeId": report.nodeid, "outcome": "error"})
        elif report.skipped:
            self.errors.append({"nodeId": report.nodeid, "outcome": "skipped"})

    def pytest_collection_finish(self, session):
        selected = {item.nodeid for item in session.items}
        trials = []
        for item in self.items:
            callspec = getattr(item, "callspec", None)
            original = getattr(item, "originalname", None)
            case = item.nodeid.rsplit("::", 1)[0] + "::" + original if original else item.nodeid
            trials.append({"id": item.nodeid, "nodeId": item.nodeid, "caseId": case,
                           "paramId": callspec.id if callspec else None,
                           "parameters": {k: _parameter(v) for k, v in callspec.params.items()} if callspec else {},
                           "markers": sorted({mark.name for mark in item.iter_markers()}),
                           "selection": "selected" if item.nodeid in selected else "deselected",
                           "context": deepcopy(self.context)})
        suite = {"schemaVersion": SCHEMA_VERSION, "kind": "suite",
                 "suiteId": self.config.getoption("eval_suite_id"), "trials": trials,
                 "collection": {"complete": not any(e["outcome"] == "error" for e in self.errors),
                                "observations": deepcopy(self.errors)}}
        if self.source is not None:
            suite["source"] = deepcopy(self.source)
        if self.workflow is not None:
            suite["workflow"] = deepcopy(self.workflow)
        path = self.config.getoption("eval_discovery")
        if path:
            atomic_write(Path(path), suite)
        if self.config.getoption("eval_output"):
            try:
                self.recorder = Recorder(suite, self.config.getoption("eval_output"),
                                         run_id=self.config.getoption("eval_run_id"),
                                         job_id=self.config.getoption("eval_job_id"),
                                         attempt_id=self.config.getoption("eval_attempt_id"),
                                         attempt_number=self.config.getoption("eval_attempt_number"),
                                         resume_from=self.config.getoption("eval_resume_from"),
                                         prior_stopped=self.config.getoption("eval_prior_stopped"))
            except (ValueError, FileExistsError) as exc:
                raise pytest.UsageError(str(exc)) from None

    def pytest_runtest_logstart(self, nodeid, location):
        if self.recorder:
            self.recorder.trial(nodeid).start()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        result = yield
        report = result.get_result()
        outcome = report.outcome
        if hasattr(report, "wasxfail"):
            outcome = "xfailed" if report.skipped else "xpassed"
        elif report.when == "call" and report.failed and call.excinfo is None:
            # Pytest converts strict unexpected passes into failed reports.
            outcome = "xpassed_strict" if str(report.longrepr).startswith("[XPASS(strict)]") else "failed"
        report._dreamlake_outcome = outcome

    def pytest_runtest_logreport(self, report):
        if not self.recorder:
            return
        trial = self.recorder.trial(report.nodeid)
        trial.phase(report.when, getattr(report, "_dreamlake_outcome", report.outcome), duration=report.duration)
        # Passing call is provisional until every fixture finalizer has run.
        if report.when != "teardown":
            return
        attempt = next(t for t in self.recorder.snapshot()["trials"] if t["id"] == report.nodeid)["attempt"]
        phases = attempt["phases"]
        outcomes = {k: v["outcome"] for k, v in phases.items()}
        if any(outcomes.get(k) == "failed" for k in ("setup", "teardown")):
            trial.complete("error", "inconclusive", reason="fixture_error")
        elif any(v == "xpassed_strict" for v in outcomes.values()):
            trial.complete("completed", "failed", reason="xpassed_strict")
        elif outcomes.get("call") == "failed":
            trial.complete("completed", "failed", reason="assertion_or_test_error")
        elif any(v == "xfailed" for v in outcomes.values()):
            trial.complete("completed", "inconclusive", reason="xfailed")
        elif any(v == "skipped" for v in outcomes.values()):
            trial.complete("completed", "not_evaluated", reason="skipped")
        elif outcomes.get("call") in {"passed", "xpassed"}:
            # An explicit intermediate validation can fail even if Python returned normally.
            verdict = attempt["verdict"] if attempt["verdict"] != "not_evaluated" else "passed"
            trial.complete("completed", verdict, reason="xpassed" if outcomes["call"] == "xpassed" else "pytest")
        else:
            trial.complete("unknown", "inconclusive", reason="missing_call_outcome")

    def pytest_keyboard_interrupt(self, excinfo):
        self.interrupted = True

    def pytest_sessionfinish(self, session, exitstatus):
        if self.recorder and not self.recorder._failed:
            self.recorder.finish(exit_code=int(exitstatus), interrupted=self.interrupted)


@pytest.fixture
def eval_trial(request):
    """Explicit metrics/references; no automatic stdout, log or artifact capture."""
    adapter = request.config.pluginmanager.getplugin("dreamlake-eval-recorder")
    if adapter is None or adapter.recorder is None:
        raise pytest.UsageError("eval_trial requires --eval-output, --eval-run-id and --eval-attempt-id")
    return adapter.recorder.trial(request.node.nodeid)
