from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from dreamlake_eval import (Recorder, manifest, merge_reports, replay, workflow_snapshot,
                           validate_workflow, read_workflow, write_workflow)
from test_model import suite

SOURCE = '''name: Evaluation
on: [workflow_dispatch]
jobs:
  cpu:
    runs-on: ubuntu-latest
    steps:
      - run: pytest tests/
'''


def capture(content=SOURCE, format="yaml", revision="abc123"):
    return workflow_snapshot(content, format=format, provider="github", revision=revision,
                             path=".github/workflows/evaluation.yml")


def test_json_capture_retains_exact_authored_text_and_on(tmp_path):
    original = SOURCE.replace("\n", "\r\n")
    snapshot = capture(original)
    assert snapshot["definition"]["on"] == ["workflow_dispatch"]
    assert snapshot["source"]["content"] == original
    assert "trials" not in snapshot["definition"]
    path = tmp_path / "workflow.json"
    write_workflow(path, snapshot)
    assert read_workflow(path) == snapshot == json.loads(path.read_text())
    # Persistence is JSON even when a caller chooses an unusual suffix.
    write_workflow(tmp_path / "unusual.yaml", snapshot)
    assert read_workflow(tmp_path / "unusual.yaml") == snapshot
    path.write_text(SOURCE)
    with pytest.raises(ValueError, match="not YAML"):
        read_workflow(path)


@pytest.mark.parametrize("raw", [
    'jobs: {}\njobs: {}', 'jobs: !!python/object:object {}', 'jobs: {1: bad}',
    'jobs: {value: .nan}', 'jobs: {value: 2026-11-27}',
    'jobs: &recursive [*recursive]', 'jobs: &a {}\ncopy: *a',
    '%YAML 1.1\n---\non: [push]', 'jobs: {}\n---\njobs: {}', '[not, object]',
])
def test_reject_non_json_ambiguous_or_alias_yaml(raw):
    with pytest.raises(ValueError):
        capture(raw)


@pytest.mark.parametrize("raw", ['{"jobs":{},"jobs":{}}', '{"x":NaN}', '[1]', '{"x":Infinity}'])
def test_reject_non_json_or_duplicate_json(raw):
    with pytest.raises(ValueError):
        capture(raw, format="json")


def test_validate_mismatch_before_any_recording_and_hash_pins_revision(tmp_path):
    snapshot = capture()
    for mutate in [lambda s: s["definition"].update(jobs={}),
                   lambda s: s["source"].update(content=SOURCE + "# changed\n"),
                   lambda s: s.update(definitionSha256="f" * 64)]:
        changed = deepcopy(snapshot); mutate(changed)
        inventory = {**suite(), "workflow": changed}
        with pytest.raises(ValueError):
            Recorder(inventory, tmp_path / "bad", run_id="e", attempt_id="a")
        assert not (tmp_path / "bad").exists()
    # JSON equality must not treat booleans as numeric ones.
    typed = capture('{"x":true}', format="json")
    typed["definition"]["x"] = 1
    with pytest.raises(ValueError): validate_workflow(typed)
    inventory = {**suite(), "workflow": snapshot}
    first = Recorder(inventory, tmp_path / "first", run_id="e", attempt_id="a")
    first.finish(interrupted=True)
    assert read_workflow(tmp_path / "first/workflow.json") == snapshot
    assert replay(tmp_path / "first/events.jsonl") == first.snapshot()
    changed = {**suite(), "workflow": capture(revision="different")}
    with pytest.raises(ValueError, match="same run/definition"):
        Recorder(changed, tmp_path / "retry", run_id="e", attempt_id="b", attempt_number=2,
                 resume_from=tmp_path / "first/eval.json", prior_stopped=True)
    assert not (tmp_path / "retry").exists()
    retry = Recorder(inventory, tmp_path / "retry", run_id="e", attempt_id="b", attempt_number=2,
                     resume_from=tmp_path / "first/eval.json", prior_stopped=True)
    assert retry.snapshot()["workflow"] == snapshot
    assert merge_reports(manifest("e", {"local": inventory}), [first.snapshot()])["jobs"][0]["workflow"] == snapshot
    events = [json.loads(line) for line in (tmp_path / "first/events.jsonl").read_text().splitlines()]
    from dreamlake_eval.model import definition_hash
    events[-1]["changes"].update(workflow=changed["workflow"], definitionHash=definition_hash(changed))
    tampered = tmp_path / "tampered.jsonl"
    tampered.write_text("".join(json.dumps(event) + "\n" for event in events))
    with pytest.raises(ValueError, match="definition cannot change"):
        replay(tampered)
    with pytest.raises(ValueError, match="changed inventory"):
        merge_reports(manifest("e", {"local": changed}), [first.snapshot()])


def test_core_json_native_preserves_existing_spec_and_limits():
    spec = {"version": 1, "stages": [], "nodes": [], "edges": []}
    snapshot = workflow_snapshot(json.dumps(spec), format="json", provider="dreamlake", revision="v7", path="workflow.json")
    assert validate_workflow(snapshot)["definition"] == spec
    with pytest.raises(ValueError, match="1 MiB"): capture(" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError): capture('{"x":' * 110 + '0' + '}' * 110, format="json")


def test_cli_capture_discovery_recording_and_yaml_rejection(tmp_path):
    source = tmp_path / "source.yml"; source.write_text(SOURCE)
    target = tmp_path / "workflow.json"
    def run(*args):
        return subprocess.run([sys.executable, *args], cwd=tmp_path, capture_output=True, text=True)
    made = run("-m", "dreamlake_eval.cli", "workflow", str(source), "--format", "yaml", "--provider", "github",
               "--revision", "abc123", "--path", ".github/workflows/evaluation.yml", "--output", str(target))
    assert made.returncode == 0, made.stderr
    (tmp_path / "test_example.py").write_text('def test_ok(): assert True\n')
    discovered = run("-m", "dreamlake_eval.cli", "discover", "--output", "suite.json", "--workflow", str(target), "--", "test_example.py")
    assert discovered.returncode == 0, discovered.stdout + discovered.stderr
    executed = run("-m", "pytest", "test_example.py", "--eval-workflow", str(target), "--eval-output", "report",
                   "--eval-run-id", "e", "--eval-attempt-id", "a")
    assert executed.returncode == 0, executed.stdout + executed.stderr
    inventory = json.loads((tmp_path / "suite.json").read_text())
    recorded = json.loads((tmp_path / "report/eval.json").read_text())
    assert inventory["workflow"] == recorded["workflow"] == read_workflow(target)
    assert merge_reports(manifest("e", {"local": inventory}), [recorded])["complete"]
    refused = run("-m", "pytest", "test_example.py", "--eval-workflow", str(source), "--eval-output", "refused",
                  "--eval-run-id", "e", "--eval-attempt-id", "b")
    assert refused.returncode == 4 and "not YAML" in refused.stderr
    assert not (tmp_path / "refused").exists()
