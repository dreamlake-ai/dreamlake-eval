from copy import deepcopy
import json
from pathlib import Path
import pytest

from dreamlake_eval import Recorder, replay, export_cict, manifest, merge_reports


def suite(context=None):
    return {"schemaVersion": "dreamlake.eval/v1", "kind": "suite", "suiteId": "simulation",
            "collection": {"complete": True, "observations": []},
            "trials": [{"id": "test_sim.py::test_case[seed-7]", "nodeId": "test_sim.py::test_case[seed-7]",
                        "caseId": "test_sim.py::test_case", "paramId": "seed-7", "parameters": {"seed": 7},
                        "markers": [], "selection": "selected", "context": context or {}}]}


def create(tmp_path, **kw):
    return Recorder(suite(), tmp_path, run_id="evaluation", attempt_id=kw.pop("attempt_id", "a1"), **kw)


def test_metrics_verdict_journal_and_detached_values(tmp_path):
    recorder = create(tmp_path / "run")
    trial = recorder.trial(suite()["trials"][0]["id"])
    trial.start()
    trial.metric("reward", 0, step=1)
    trial.verdict("failed", policy={"id": "reward-floor", "version": "1"})
    assert recorder.snapshot()["trials"][0]["attempt"]["execution"] == "running"
    trial.checkpoint("file:///explicit-checkpoint", attributes={"epoch": 1})
    trial.artifact("https://example.org/explicit-result")
    trial.complete("completed", "failed")
    recorder.finish(exit_code=1)
    snapshot = recorder.snapshot()
    assert replay(tmp_path / "run/events.jsonl") == snapshot
    assert export_cict(snapshot)["records"][0]["metrics"] == {"reward": 0}
    assert export_cict(snapshot)["records"][0]["status"] == "failed"
    assert snapshot["trials"][0]["attempt"]["policy"]["version"] == "1"
    snapshot["trials"].clear()
    assert len(recorder.snapshot()["trials"]) == 1
    assert not hasattr(recorder, "_events")
    with pytest.raises(ValueError): trial.metric("late", 1)


def test_no_overwrite_resume_requires_reconciled_new_attempt(tmp_path):
    recorder = create(tmp_path / "a1")
    node = suite()["trials"][0]["id"]
    recorder.trial(node).start()
    recorder.trial(node).checkpoint("file:///saved")
    recorder.finish(interrupted=True)
    with pytest.raises(FileExistsError): create(tmp_path / "a1")
    with pytest.raises(ValueError): create(tmp_path / "a2", attempt_id="a2", attempt_number=2, resume_from=tmp_path / "a1/eval.json")
    with pytest.raises(ValueError): create(tmp_path / "a2", attempt_id="a1", attempt_number=2, prior_stopped=True, resume_from=tmp_path / "a1/eval.json")
    new = create(tmp_path / "a2", attempt_id="a2", attempt_number=2, prior_stopped=True, resume_from=tmp_path / "a1/eval.json")
    assert new.trial(node).resume_checkpoints[0]["uri"] == "file:///saved"
    assert new.snapshot()["trials"][0]["attempt"]["execution"] == "not_run"


def test_replay_dedup_conflict_gap_and_truncated_tail(tmp_path):
    recorder = create(tmp_path / "run")
    recorder.finish()
    lines = (tmp_path / "run/events.jsonl").read_bytes().splitlines(keepends=True)
    duplicate = tmp_path / "duplicate"
    duplicate.write_bytes(lines[0] + lines[0] + lines[1])
    assert replay(duplicate) == recorder.snapshot()
    changed = json.loads(lines[0]); changed["time"] = "changed"
    duplicate.write_bytes(lines[0] + json.dumps(changed).encode() + b"\n")
    with pytest.raises(ValueError, match="conflicting"): replay(duplicate)
    duplicate.write_bytes(lines[1])
    with pytest.raises(ValueError, match="gap"): replay(duplicate)
    duplicate.write_bytes(b"".join(lines) + b'{"sequence":')
    with pytest.raises(ValueError, match="incomplete"): replay(duplicate)
    assert replay(duplicate, allow_truncated_tail=True) == recorder.snapshot()


def test_failure_after_commit_does_not_retry_or_lose_journal(tmp_path):
    def sink(event):
        if event["sequence"] == 2: raise RuntimeError("sink unavailable")
    recorder = create(tmp_path / "run", on_event=sink)
    with pytest.raises(RuntimeError): recorder.trial(suite()["trials"][0]["id"]).start()
    recovered = replay(tmp_path / "run/events.jsonl")
    assert recovered["trials"][0]["attempt"]["execution"] == "running"
    with pytest.raises(RuntimeError): recorder.finish()
    assert len((tmp_path / "run/events.jsonl").read_text().splitlines()) == 2


def test_invalid_measurements_are_atomic_and_graph_retains_alternatives(tmp_path):
    recorder = create(tmp_path / "run")
    trial = recorder.trial(suite()["trials"][0]["id"]); trial.start()
    before = recorder.snapshot()
    for value in [True, float("nan"), float("inf"), 10**1000, "1"]:
        with pytest.raises(ValueError): trial.metric("x", value)
    assert recorder.snapshot() == before
    trial.node("choose", kind="decision"); trial.node("small"); trial.node("large")
    trial.edge("a", "choose", "small"); trial.edge("b", "choose", "large")
    trial.select("choose", "a"); trial.step("small", "running"); trial.step("small", "completed")
    graph = recorder.snapshot()["trials"][0]["attempt"]["graph"]
    assert [e["selected"] for e in graph["edges"]] == [True, False]
    assert graph["nodes"][2]["execution"] == "not_run"


def test_aggregate_inventory_missing_retry_and_out_of_order(tmp_path):
    plan = manifest("evaluation", {"machine-a": suite(), "machine-b": suite({"device": "gpu"})})
    a = create(tmp_path / "a", job_id="machine-a")
    trial = a.trial(suite()["trials"][0]["id"]); trial.start(); trial.metric("score", 0); trial.complete("completed", "passed"); a.finish()
    first = merge_reports(plan, [a.snapshot(), a.snapshot()])
    assert not first["complete"] and first["rows"][1]["availability"] == "missing"
    assert first["rows"][1]["attempt"] is None
    assert first["rows"][0]["attempt"]["metrics"]["score"] == 0
    b = Recorder(suite({"device": "gpu"}), tmp_path / "b", run_id="evaluation", job_id="machine-b", attempt_id="b1")
    b.finish()
    retry = create(tmp_path / "retry", job_id="machine-a", attempt_id="a2", attempt_number=2)
    merged = merge_reports(plan, [retry.snapshot(), b.snapshot(), a.snapshot()])
    assert not merged["complete"]
    assert merged["rows"][0]["attempt"]["execution"] == "not_run"
    assert len({r["id"] for r in merged["rows"]}) == 2
    assert merged == merge_reports(plan, [a.snapshot(), b.snapshot(), retry.snapshot()])
    conflict = deepcopy(a.snapshot()); conflict["attemptId"] = "different"
    with pytest.raises(ValueError, match="conflicting attempt"): merge_reports(plan, [a.snapshot(), conflict])
    conflict = deepcopy(a.snapshot()); conflict["trials"][0]["attempt"]["metrics"]["score"] = 99
    with pytest.raises(ValueError, match="conflicting report"): merge_reports(plan, [a.snapshot(), conflict])


def test_incomplete_collection_cannot_be_complete(tmp_path):
    definition = suite(); definition["collection"]["complete"] = False
    recorder = Recorder(definition, tmp_path / "run", run_id="evaluation", attempt_id="a1")
    recorder.finish(exit_code=2)
    assert not merge_reports(manifest("evaluation", {"local": definition}), [recorder.snapshot()])["complete"]


def test_incomplete_tests_and_terminal_regression_are_not_complete(tmp_path):
    a = create(tmp_path / "a")
    a.finish()
    plan = manifest("evaluation", {"local": suite()})
    assert not merge_reports(plan, [a.snapshot()])["complete"]
    b = create(tmp_path / "b", attempt_id="b")
    trial = b.trial(suite()["trials"][0]["id"]); trial.start(); trial.complete("completed", "failed")
    failed = b.snapshot()
    regression = deepcopy(failed); regression["sequence"] += 1
    regression["trials"][0]["attempt"]["verdict"] = "passed"
    with pytest.raises(ValueError, match="terminal trial"):
        merge_reports(plan, [regression, failed])
    reused = deepcopy(failed); reused["attemptNumber"] = 2
    with pytest.raises(ValueError, match="reused"):
        merge_reports(plan, [failed, reused])


def test_per_metric_files_preserve_zero_and_missing(tmp_path):
    from dreamlake_eval import write_aggregate
    plan = manifest("evaluation", {"local": suite(), "missing": suite()})
    recorder = create(tmp_path / "attempt")
    t = recorder.trial(suite()["trials"][0]["id"]); t.start(); t.metric("reward.with/slashes", 0); t.complete("completed", "passed")
    recorder.finish()
    output = tmp_path / "aggregate"
    result = write_aggregate(plan, [tmp_path / "attempt/eval.json"], output, metrics=["reward.with/slashes", "absent"])
    assert not result["complete"]
    for view in result["views"]:
        path = output / view["directory"]
        assert path.parent == output
        data = json.loads((path / "cict.json").read_text())
        assert data["records"][1]["metrics"] == {}
        assert data["records"][1]["status"] == "unknown"
        if view["metric"] == "reward.with/slashes":
            assert data["records"][0]["metrics"] == {"value": 0}
        else:
            assert data["records"][0]["metrics"] == {}
        assert json.loads((path / ".dreamrc").read_text())["views"][0]["value"] == "metrics.value"


def test_snapshot_failure_preserves_replayable_committed_event(tmp_path, monkeypatch):
    import dreamlake_eval.model as model
    recorder = create(tmp_path / "attempt")
    def fail(*args): raise OSError("disk full")
    monkeypatch.setattr(model, "atomic_write", fail)
    with pytest.raises(OSError): recorder.trial(suite()["trials"][0]["id"]).start()
    assert replay(tmp_path / "attempt/events.jsonl")["trials"][0]["attempt"]["execution"] == "running"
    with pytest.raises(RuntimeError): recorder.finish()


def test_cli_manifest_and_aggregate_use_explicit_files(tmp_path):
    import subprocess
    import sys
    definition = tmp_path / "suite.json"
    definition.write_text(json.dumps(suite()))
    recorder = create(tmp_path / "attempt")
    trial = recorder.trial(suite()["trials"][0]["id"]); trial.start(); trial.complete("completed", "passed"); recorder.finish()
    command = [sys.executable, "-m", "dreamlake_eval.cli"]
    subprocess.run(command + ["manifest", "--evaluation-id", "evaluation", "--job", f"local={definition}", "--output", str(tmp_path / "manifest.json")], check=True)
    subprocess.run(command + ["aggregate", "--manifest", str(tmp_path / "manifest.json"), "--report", str(tmp_path / "attempt/eval.json"), "--output", str(tmp_path / "result"), "--metric", "reward"], check=True)
    result = json.loads((tmp_path / "result/aggregate.json").read_text())
    assert result["complete"] is True
    config = json.loads((tmp_path / "result/.dreamrc").read_text())
    assert config["version"] == 1 and config["views"][0]["view"] == "activityHeatmap"


def test_invalid_manifest_and_attributes_rejected_before_write(tmp_path):
    plan = manifest("evaluation", {"local": suite()})
    plan["jobs"][0]["suite"]["trials"][0]["context"] = {"changed": True}
    with pytest.raises(ValueError, match="manifest inventory"):
        merge_reports(plan, [])
    recorder = create(tmp_path / "attempt")
    trial = recorder.trial(suite()["trials"][0]["id"]); trial.start()
    before = recorder.snapshot()
    with pytest.raises(ValueError): trial.node("bad", attributes=[])
    with pytest.raises(ValueError): trial.metric("bad", 1, attributes=[])
    with pytest.raises(ValueError): trial.checkpoint("file:///x", attributes=[])
    with pytest.raises(ValueError): trial.phase("setup", "passed", duration=-1)
    with pytest.raises(ValueError): recorder.finish(exit_code=True)
    assert recorder.snapshot() == before
