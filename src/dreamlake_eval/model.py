"""Single-writer evaluation journal; execution remains owned by the caller."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

SCHEMA_VERSION = "dreamlake.eval/v1"
VERDICTS = {"not_evaluated", "passed", "failed", "inconclusive"}
EXECUTIONS = {"not_run", "running", "completed", "error", "interrupted", "cancelled", "timed_out", "unknown"}


def text(value: str) -> str:
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise ValueError("expected nonempty text without control characters")
    return value


def json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, list):
        return [json_value(v) for v in value]
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        return {k: json_value(v) for k, v in value.items()}
    raise ValueError("expected finite JSON data, not Python objects")


def number(value: float) -> float:
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("metric values must be finite numbers")
    return value


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        if hasattr(os, "O_DIRECTORY"):
            parent = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_suite(suite: dict) -> dict:
    suite = json_value(suite)
    if not isinstance(suite, dict) or suite.get("schemaVersion") != SCHEMA_VERSION or suite.get("kind") != "suite":
        raise ValueError("expected a dreamlake.eval/v1 suite")
    text(suite.get("suiteId"))
    if not isinstance(suite.get("trials"), list) or not isinstance(suite.get("collection"), dict):
        raise ValueError("suite requires trials and collection metadata")
    if type(suite["collection"].get("complete")) is not bool:
        raise ValueError("collection.complete must be a boolean")
    if "source" in suite:
        if not isinstance(suite["source"], dict):
            raise ValueError("source must be a provider provenance object")
        text(suite["source"].get("provider"))
    ids = set()
    for trial in suite["trials"]:
        if not isinstance(trial, dict) or text(trial.get("id")) in ids:
            raise ValueError("trial IDs must be unique")
        ids.add(trial["id"])
        text(trial.get("nodeId"))
        text(trial.get("caseId"))
        if trial.get("selection") not in {"selected", "deselected"}:
            raise ValueError("selection must be selected or deselected")
    return suite


def definition_hash(suite: dict) -> str:
    # Selection can change on a deliberate retry; identities/parameter metadata cannot.
    inventory = [{k: v for k, v in trial.items() if k != "selection"} for trial in suite["trials"]]
    return hashlib.sha256(encoded({"suiteId": suite["suiteId"], "source": suite.get("source"), "trials": sorted(inventory, key=lambda r: r["id"])})).hexdigest()


def validate_report(report: dict) -> dict:
    report = json_value(report)
    if not isinstance(report, dict) or report.get("schemaVersion") != SCHEMA_VERSION or report.get("kind") != "run":
        raise ValueError("expected a dreamlake.eval/v1 run report")
    for key in ("runId", "evaluationId", "jobId", "attemptId", "suiteId", "definitionHash"):
        text(report.get(key))
    if report["runId"] != report["evaluationId"] or report.get("execution") not in EXECUTIONS:
        raise ValueError("invalid evaluation identity or execution")
    for key in ("attemptNumber", "sequence"):
        if type(report.get(key)) is not int or report[key] < 1:
            raise ValueError("invalid attempt number or sequence")
    definition = {**report, "kind": "suite", "trials": [
        {k: v for k, v in trial.items() if k != "attempt"} for trial in report.get("trials", [])]}
    validate_suite(definition)
    if definition_hash(definition) != report["definitionHash"]:
        raise ValueError("report inventory does not match its definition hash")
    ids = set()
    for trial in report["trials"]:
        if not isinstance(trial, dict) or text(trial.get("id")) in ids:
            raise ValueError("trial IDs must be unique")
        ids.add(trial["id"])
        attempt = trial.get("attempt")
        if not isinstance(attempt, dict) or attempt.get("id") != report["attemptId"] or attempt.get("execution") not in EXECUTIONS or attempt.get("verdict") not in VERDICTS:
            raise ValueError("invalid trial attempt")
        if not isinstance(attempt.get("metrics"), dict):
            raise ValueError("metrics must be an object")
        for value in attempt["metrics"].values():
            number(value)
    return report


def read_report(path: str | Path) -> dict:
    return validate_report(json.loads(Path(path).read_text(encoding="utf-8")))


def validate_progress(previous: dict, current: dict) -> None:
    """Reject regressions within one attempt; retry belongs to another identity."""
    if previous["execution"] != "running":
        if {k: v for k, v in previous.items() if k != "sequence"} != {k: v for k, v in current.items() if k != "sequence"}:
            raise ValueError("terminal run outcome cannot change")
    before = {t["id"]: t for t in previous["trials"]}
    for trial in current["trials"]:
        old = before[trial["id"]]["attempt"]
        new = trial["attempt"]
        if old["execution"] not in {"not_run", "running"} and old != new:
            raise ValueError("terminal trial outcome cannot change")
        if old["execution"] == "running" and new["execution"] == "not_run":
            raise ValueError("trial execution cannot regress")


def export_cict(report: dict) -> dict:
    """Explicit lossy projection. Full execution/verdict remain in attributes."""
    records = []
    for trial in report["trials"]:
        attempt = trial["attempt"]
        execution, verdict = attempt["execution"], attempt["verdict"]
        status = "unknown"
        if execution in {"not_run", "running"}:
            status = execution
        elif execution == "completed":
            if verdict in {"passed", "failed"}:
                status = verdict
            elif attempt.get("reason") in {"skipped", "xfailed"}:
                status = "skipped"
        records.append({"id": trial["id"], "testId": trial["nodeId"], "name": trial["nodeId"],
                        "kind": "test", "status": status, "metrics": deepcopy(attempt["metrics"]),
                        "attributes": {**deepcopy(attempt["attributes"]), "suite": report["suiteId"],
                                       "caseId": trial["caseId"], "selection": trial["selection"],
                                       "execution": execution, "verdict": verdict,
                                       "attemptId": attempt["id"], "reason": attempt.get("reason")}})
    return {"schemaVersion": "dreamlake.cict/v1", "kind": "run", "suiteId": report["suiteId"],
            "name": report["suiteId"], "runId": report["runId"], "records": records, "edges": []}


def replay(path: str | Path, *, allow_truncated_tail: bool = False) -> dict:
    """Replay persisted facts, not workload code. Unknown remains unknown.

    Identical duplicate events are accepted; conflicting IDs and sequence gaps
    are refused. Only an explicitly permitted incomplete final line is ignored.
    """
    state = None
    seen = {}
    with Path(path).open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                if allow_truncated_tail and not stream.read(1):
                    break
                raise ValueError("incomplete event line")
            event = json.loads(line)
            seq = event.get("sequence")
            if type(seq) is not int or seq < 1 or event.get("schemaVersion") != SCHEMA_VERSION:
                raise ValueError("invalid event envelope")
            digest = hashlib.sha256(encoded(event)).hexdigest()
            if seq in seen:
                if seen[seq] != digest:
                    raise ValueError("conflicting event replay")
                continue
            if seq != len(seen) + 1:
                raise ValueError("event sequence gap")
            previous = deepcopy(state)
            if state is None:
                if event.get("type") != "run.created":
                    raise ValueError("journal must begin with run.created")
                state = deepcopy(event["snapshot"])
            else:
                if (event.get("runId"), event.get("jobId"), event.get("attemptId")) != (state["runId"], state["jobId"], state["attemptId"]):
                    raise ValueError("mixed run or attempt identity")
                if event["type"] == "run.finished":
                    state.update(event["changes"])
                elif "trial" in event:
                    index = next((i for i, trial in enumerate(state["trials"]) if trial["id"] == event["trial"]["id"]), None)
                    if index is None:
                        raise ValueError("unknown trial in event")
                    state["trials"][index] = deepcopy(event["trial"])
                else:
                    raise ValueError("unrecognized event")
            seen[seq] = digest
            state["sequence"] = seq
            if (event.get("runId"), event.get("jobId"), event.get("attemptId")) != (state["runId"], state["jobId"], state["attemptId"]):
                raise ValueError("mixed run or attempt identity")
            validate_report(state)
            if previous:
                validate_progress(previous, state)
    if state is None:
        raise ValueError("empty journal")
    return state


class Recorder:
    """Persist one explicitly named attempt to a new directory.

    Events are fsynced before publishing snapshots. A failed sink or snapshot
    write propagates; the committed journal remains authoritative. No in-memory
    event list, automatic upload, scheduler, process resume or write retry.
    """
    def __init__(self, suite: dict, output: str | Path, *, run_id: str, attempt_id: str,
                 job_id: str = "local", attempt_number: int = 1,
                 resume_from: str | Path | None = None, prior_stopped: bool = False,
                 on_event: Callable[[dict], None] | None = None):
        suite = validate_suite(suite)
        run_id, attempt_id, job_id = text(run_id), text(attempt_id), text(job_id)
        if type(attempt_number) is not int or attempt_number < 1:
            raise ValueError("attempt_number must be a positive integer")
        previous = None
        if resume_from is not None:
            if prior_stopped is not True:
                raise ValueError("reconcile prior execution and explicitly confirm prior_stopped")
            previous = read_report(resume_from)
            if previous["runId"] != run_id or previous.get("jobId") != job_id or previous["attemptId"] == attempt_id or previous["definitionHash"] != definition_hash(suite) or attempt_number <= previous.get("attemptNumber", 0):
                raise ValueError("resume requires same run/definition and a new attempt ID")
        self.output = Path(output)
        # Exclusive creation prevents accidental overwrite and competing writers.
        self.output.mkdir(parents=True, exist_ok=False)
        self._on_event = on_event
        self._failed = False
        self._previous = {t["id"]: t for t in previous["trials"]} if previous else {}
        self._data = {**suite, "kind": "run", "runId": run_id, "evaluationId": run_id,
                      "jobId": job_id, "attemptId": attempt_id, "attemptNumber": attempt_number,
                      "definitionHash": definition_hash(suite), "sequence": 0,
                      "execution": "running", "startedAt": timestamp()}
        if previous:
            self._data["resumes"] = {"runId": run_id, "attemptId": previous["attemptId"],
                                     "report": str(resume_from), "priorStoppedConfirmed": True}
        for trial in self._data["trials"]:
            trial["attempt"] = {"id": attempt_id, "execution": "not_run", "verdict": "not_evaluated",
                                "metrics": {}, "attributes": {}, "phases": {}, "checkpoints": [],
                                "artifacts": [], "graph": {"nodes": [], "edges": []}}
        self._commit("run.created", snapshot=deepcopy(self._data))

    def _commit(self, event_type: str, **payload: Any) -> None:
        if self._failed:
            raise RuntimeError("recorder failed; reconcile journal before creating another attempt")
        event = {"schemaVersion": SCHEMA_VERSION, "runId": self._data["runId"],
                 "evaluationId": self._data["runId"], "jobId": self._data["jobId"],
                 "attemptId": self._data["attemptId"], "sequence": self._data["sequence"] + 1,
                 "time": timestamp(), "type": event_type, **payload}
        data = encoded(event) + b"\n"
        try:
            with (self.output / "events.jsonl").open("ab") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if "trial" in payload:
                index = next(i for i, t in enumerate(self._data["trials"]) if t["id"] == payload["trial"]["id"])
                self._data["trials"][index] = deepcopy(payload["trial"])
            if "changes" in payload:
                self._data.update(payload["changes"])
            self._data["sequence"] = event["sequence"]
            atomic_write(self.output / "eval.json", self._data)
            atomic_write(self.output / "cict.json", export_cict(self._data))
            if self._on_event:
                self._on_event(deepcopy(event))
        except BaseException:
            self._failed = True
            raise

    def snapshot(self) -> dict:
        return deepcopy(self._data)

    def trial(self, node_id: str) -> "Trial":
        if node_id not in {t["id"] for t in self._data["trials"]}:
            raise ValueError("unknown trial")
        return Trial(self, node_id)

    def finish(self, *, exit_code: int = 0, interrupted: bool = False) -> None:
        if type(exit_code) is not int or type(interrupted) is not bool:
            raise ValueError("exit_code must be an integer and interrupted a boolean")
        if self._data["execution"] != "running":
            raise ValueError("attempt already finished")
        for trial in list(self._data["trials"]):
            if trial["attempt"]["execution"] == "running":
                self.trial(trial["id"]).complete("interrupted" if interrupted else "unknown", "inconclusive", reason="unfinished")
        state = "interrupted" if interrupted else "error" if exit_code in {2, 3, 4} else "completed"
        self._commit("run.finished", changes={"execution": state, "exitCode": exit_code, "finishedAt": timestamp()})


class Trial:
    def __init__(self, recorder: Recorder, node_id: str):
        self.recorder, self.id = recorder, node_id

    def _copy(self, *, running: bool = False) -> dict:
        if self.recorder._data["execution"] != "running":
            raise ValueError("run attempt is terminal")
        trial = deepcopy(next(t for t in self.recorder._data["trials"] if t["id"] == self.id))
        if running and trial["attempt"]["execution"] != "running":
            raise ValueError("trial must be running")
        return trial

    def _save(self, event: str, trial: dict, **payload: Any) -> None:
        self.recorder._commit(event, trial=trial, trialId=self.id, **payload)

    @property
    def resume_checkpoints(self) -> list:
        return deepcopy(self.recorder._previous.get(self.id, {}).get("attempt", {}).get("checkpoints", []))

    def start(self) -> None:
        trial = self._copy()
        if trial["selection"] != "selected" or trial["attempt"]["execution"] != "not_run":
            raise ValueError("only a selected not_run trial can start")
        trial["attempt"].update(execution="running", startedAt=timestamp())
        self._save("trial.started", trial)

    def metric(self, name: str, value: float, *, step: int | None = None, attributes: dict | None = None) -> None:
        text(name); number(value)
        if step is not None and (type(step) is not int or step < 0):
            raise ValueError("step must be a nonnegative integer")
        attrs = json_value(attributes if attributes is not None else {})
        if not isinstance(attrs, dict):
            raise ValueError("attributes must be an object")
        trial = self._copy(running=True)
        trial["attempt"]["metrics"][name] = value
        self._save("metric.sample", trial, sample={"name": name, "value": value, "step": step, "attributes": attrs})

    def verdict(self, value: str, *, reason: str | None = None, policy: dict | None = None) -> None:
        if value not in VERDICTS:
            raise ValueError("invalid verdict")
        trial = self._copy(running=True)
        trial["attempt"]["verdict"] = value
        if reason is not None:
            trial["attempt"]["reason"] = text(reason)
        if policy is not None:
            policy = json_value(policy)
            if not isinstance(policy, dict) or not all(isinstance(policy.get(k), str) and policy[k] for k in ("id", "version")):
                raise ValueError("policy provenance requires id and version")
            trial["attempt"]["policy"] = policy
        self._save("verdict.recorded", trial)

    def _reference(self, kind: str, uri: str, *, name: str | None = None, attributes: dict | None = None) -> None:
        # Store only an explicit reference; do not inspect/read/upload its target.
        attrs = json_value(attributes if attributes is not None else {})
        if not isinstance(attrs, dict):
            raise ValueError("attributes must be an object")
        ref = {"uri": text(uri), "attributes": attrs}
        if name is not None:
            ref["name"] = text(name)
        trial = self._copy(running=True)
        trial["attempt"][kind].append(ref)
        self._save("checkpoint.recorded" if kind == "checkpoints" else "artifact.recorded", trial, reference=ref)

    def checkpoint(self, uri: str, *, name: str | None = None, attributes: dict | None = None) -> None:
        self._reference("checkpoints", uri, name=name, attributes=attributes)

    def artifact(self, uri: str, *, name: str | None = None, attributes: dict | None = None) -> None:
        self._reference("artifacts", uri, name=name, attributes=attributes)

    def phase(self, name: str, outcome: str, *, duration: float = 0) -> None:
        if name not in {"setup", "call", "teardown"} or outcome not in {"passed", "failed", "skipped", "xfailed", "xpassed", "xpassed_strict"}:
            raise ValueError("invalid pytest phase")
        number(duration)
        if duration < 0:
            raise ValueError("duration cannot be negative")
        trial = self._copy(running=True)
        if name in trial["attempt"]["phases"]:
            raise ValueError("phase already recorded")
        trial["attempt"]["phases"][name] = {"outcome": outcome, "durationSeconds": duration}
        self._save("pytest.phase", trial, phase=name)

    def complete(self, execution: str, verdict: str, *, reason: str | None = None) -> None:
        if execution not in EXECUTIONS - {"not_run", "running"} or verdict not in VERDICTS:
            raise ValueError("invalid terminal outcome")
        trial = self._copy(running=True)
        trial["attempt"].update(execution=execution, verdict=verdict, finishedAt=timestamp())
        if reason is not None:
            trial["attempt"]["reason"] = text(reason)
        self._save("trial.finished", trial)

    def node(self, node_id: str, *, kind: str = "step", attributes: dict | None = None) -> None:
        if kind not in {"step", "decision"}:
            raise ValueError("kind must be step or decision")
        trial = self._copy(running=True)
        nodes = trial["attempt"]["graph"]["nodes"]
        if any(n["id"] == node_id for n in nodes):
            raise ValueError("duplicate graph node")
        attrs = json_value(attributes if attributes is not None else {})
        if not isinstance(attrs, dict):
            raise ValueError("attributes must be an object")
        nodes.append({"id": text(node_id), "kind": kind, "execution": "not_run", "attributes": attrs})
        self._save("graph.node", trial)

    def edge(self, edge_id: str, source: str, target: str) -> None:
        trial = self._copy(running=True)
        graph = trial["attempt"]["graph"]
        ids = {n["id"] for n in graph["nodes"]}
        if source not in ids or target not in ids or any(e["id"] == edge_id for e in graph["edges"]):
            raise ValueError("invalid edge identity or endpoints")
        source_node = next(n for n in graph["nodes"] if n["id"] == source)
        if source_node["kind"] == "decision" and source_node["execution"] != "not_run":
            raise ValueError("declare decision alternatives before selecting")
        graph["edges"].append({"id": text(edge_id), "source": source, "target": target, "selected": None})
        self._save("graph.edge", trial)

    def select(self, decision: str, edge_id: str) -> None:
        trial = self._copy(running=True)
        graph = trial["attempt"]["graph"]
        node = next((n for n in graph["nodes"] if n["id"] == decision and n["kind"] == "decision"), None)
        edges = [e for e in graph["edges"] if e["source"] == decision]
        if node is None or node["execution"] != "not_run" or edge_id not in {e["id"] for e in edges}:
            raise ValueError("invalid or already selected decision")
        node["execution"] = "completed"
        for edge in edges:
            edge["selected"] = edge["id"] == edge_id
        self._save("decision.selected", trial, decisionId=decision, edgeId=edge_id)

    def step(self, node_id: str, execution: str) -> None:
        trial = self._copy(running=True)
        node = next((n for n in trial["attempt"]["graph"]["nodes"] if n["id"] == node_id and n["kind"] == "step"), None)
        if node is None or execution not in EXECUTIONS - {"not_run"}:
            raise ValueError("unknown step or invalid execution state")
        if node["execution"] == "not_run" and execution != "running":
            raise ValueError("start a step before completing it")
        if node["execution"] not in {"not_run", "running"} or node["execution"] == execution:
            raise ValueError("step already recorded or terminal")
        node["execution"] = execution
        self._save("step.updated", trial, nodeId=node_id)
