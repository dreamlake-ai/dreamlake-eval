import json
from pathlib import Path
import subprocess
import sys


def load(pytester):
    return json.loads((pytester.path / "out/eval.json").read_text())


def run(pytester, *extra):
    return pytester.runpytest_subprocess("--eval-output=out", "--eval-run-id=eval", "--eval-attempt-id=a1", *extra)


def test_collection_preserves_inventory_and_deselection_without_fixture_or_test(pytester):
    pytester.makepyfile('''
import pytest
from pathlib import Path
@pytest.fixture
def expensive():
    Path("fixture-ran").touch()
@pytest.mark.parametrize("seed", [1, 2], ids=["first", "second"])
def test_sim(seed, expensive):
    Path("body-ran").touch()
''')
    result = pytester.runpytest_subprocess("--collect-only", "--eval-discovery=suite.json", "-k", "first")
    assert result.ret == 0
    suite = json.loads((pytester.path / "suite.json").read_text())
    assert len(suite["trials"]) == 2
    assert [t["paramId"] for t in suite["trials"]] == ["first", "second"]
    assert [t["selection"] for t in suite["trials"]] == ["selected", "deselected"]
    assert suite["trials"][0]["parameters"] == {"seed": 1}
    assert not (pytester.path / "fixture-ran").exists() and not (pytester.path / "body-ran").exists()


def test_all_outcomes_and_teardown_overrides_pass_without_capture(pytester):
    pytester.makepyfile('''
import pytest
@pytest.fixture
def broken_setup():
    raise RuntimeError("PRIVATE_SETUP_TEXT")
@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError("PRIVATE_TEARDOWN_TEXT")
def test_pass(eval_trial):
    print("PRIVATE_OUTPUT")
    eval_trial.metric("reward", 0, step=0)
def test_fail(): assert False
def test_setup(broken_setup): pass
def test_teardown(broken_teardown): pass
@pytest.mark.skip(reason="PRIVATE_SKIP_REASON")
def test_skip(): pass
@pytest.mark.xfail
def test_xfail(): assert False
@pytest.mark.xfail
def test_xpass(): pass
@pytest.mark.xfail(strict=True)
def test_strict(): pass
''')
    result = run(pytester)
    assert result.ret == 1
    report = load(pytester)
    states = {t["nodeId"].split("::")[-1]: t["attempt"] for t in report["trials"]}
    assert states["test_pass"]["verdict"] == "passed"
    assert states["test_pass"]["metrics"] == {"reward": 0}
    assert states["test_fail"]["verdict"] == "failed" and states["test_fail"]["execution"] == "completed"
    for name in ["test_setup", "test_teardown"]:
        assert states[name]["execution"] == "error" and states[name]["verdict"] == "inconclusive"
    assert states["test_skip"]["reason"] == "skipped"
    assert states["test_xfail"]["reason"] == "xfailed"
    assert states["test_xpass"]["reason"] == "xpassed"
    assert states["test_strict"]["reason"] == "xpassed_strict" and states["test_strict"]["verdict"] == "failed"
    for path in (pytester.path / "out").iterdir():
        assert "PRIVATE_" not in path.read_text()


def test_interruption_keeps_unstarted_and_intermediate_verdict(pytester):
    pytester.makepyfile('''
def test_a(eval_trial):
    eval_trial.verdict("failed", policy={"id": "quality", "version": "v1"})
    eval_trial.checkpoint("file:///checkpoint")
    raise KeyboardInterrupt()
def test_b(): pass
''')
    result = run(pytester)
    assert result.ret == 2
    report = load(pytester)
    assert report["execution"] == "interrupted"
    assert report["trials"][0]["attempt"]["execution"] == "interrupted"
    assert report["trials"][1]["attempt"]["execution"] == "not_run"
    assert report["trials"][0]["attempt"]["checkpoints"][0]["uri"] == "file:///checkpoint"


def test_collection_errors_are_explicit_not_a_passing_empty_suite(pytester):
    pytester.makepyfile(test_bad="raise RuntimeError('PRIVATE_IMPORT')")
    result = pytester.runpytest_subprocess("--collect-only", "--eval-discovery=suite.json")
    assert result.ret == 2
    suite = json.loads((pytester.path / "suite.json").read_text())
    assert suite["collection"]["complete"] is False
    assert suite["collection"]["observations"][0]["outcome"] == "error"
    assert "PRIVATE_IMPORT" not in json.dumps(suite)


def test_cli_discovery_and_non_json_params(pytester):
    pytester.makepyfile('''
import pytest
class Parameter:
    def __repr__(self): raise RuntimeError("must not serialize repr")
@pytest.mark.parametrize("parameter", [Parameter()], ids=["explicit-id"])
def test_case(parameter): pass
''')
    result = subprocess.run([sys.executable, "-m", "dreamlake_eval.cli", "discover", "--output", "suite.json", "--", "."], cwd=pytester.path, capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    suite = json.loads((pytester.path / "suite.json").read_text())
    assert suite["trials"][0]["paramId"] == "explicit-id"
    assert suite["trials"][0]["parameters"]["parameter"]["serialized"] is False


def test_pytest_asyncio_owns_event_loop_and_async_fixtures(pytester):
    pytester.makepyfile('''
import asyncio
import pytest
import pytest_asyncio
@pytest_asyncio.fixture
async def sample():
    await asyncio.sleep(0)
    yield 0
    await asyncio.sleep(0)
@pytest.mark.asyncio
async def test_async(sample, eval_trial):
    eval_trial.metric("reward", sample)
    await asyncio.sleep(0)
    assert sample == 0
''')
    result = run(pytester)
    assert result.ret == 0
    report = load(pytester)
    assert report["trials"][0]["attempt"]["verdict"] == "passed"
    assert report["trials"][0]["attempt"]["metrics"]["reward"] == 0


def test_source_provenance_is_explicit_and_backend_neutral(pytester):
    pytester.makepyfile('def test_case(): pass')
    result = run(pytester, '--eval-source={"provider":"dreamlake","specVersion":"v1","workflowId":"simulation"}')
    assert result.ret == 0
    assert load(pytester)["source"] == {"provider": "dreamlake", "specVersion": "v1", "workflowId": "simulation"}


def test_async_example_records_only_explicit_dash_association(tmp_path):
    from dreamlake_eval import manifest, merge_reports
    example = Path(__file__).resolve().parents[1] / "examples/multi_job"
    # This URL is a local test fixture only; no remote calls or real run claim.
    uri = "https://dreamlake.ai/example/dash/project/synthetic-async-run"
    links = tmp_path / "links.json"
    links.write_text(json.dumps({"test_async.py::test_async_trial": uri}))
    args = ["test_async.py", "--dash-links", str(links)]
    discovery = tmp_path / "suite.json"
    collected = subprocess.run([sys.executable, "-m", "dreamlake_eval.cli", "discover", "--output", str(discovery), "--", *args], cwd=example, capture_output=True, text=True)
    assert collected.returncode == 0, collected.stdout + collected.stderr
    output = tmp_path / "attempt"
    recorded = subprocess.run([sys.executable, "-m", "pytest", *args, "--eval-output", str(output), "--eval-run-id", "e", "--eval-attempt-id", "a"], cwd=example, capture_output=True, text=True)
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    report = json.loads((output / "eval.json").read_text())
    result = merge_reports(manifest("e", {"local": json.loads(discovery.read_text())}), [report])
    assert result["complete"] and result["rows"][0]["attempt"]["artifacts"][0]["uri"] == uri

    for invalid in ["https://external.invalid/example/dash/project/run", "https://dreamlake.ai/example/dash/project/run?view=logs", "https://dreamlake.ai/example/dash/project/%ZZ"]:
        links.write_text(json.dumps({"test_async.py::test_async_trial": invalid}))
        refused = subprocess.run([sys.executable, "-m", "pytest", *args, "--eval-output", str(tmp_path / "refused"), "--eval-run-id", "e", "--eval-attempt-id", "bad"], cwd=example, capture_output=True, text=True)
        assert refused.returncode == 4 and "--dash-links must map" in refused.stderr
        assert not (tmp_path / "refused").exists()
