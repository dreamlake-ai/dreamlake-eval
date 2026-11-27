# DreamLake Eval

A backend-neutral evaluation inventory, durable local event journal, and explicit multi-job reports. Pytest is the first discovery and execution adapter. Execution remains with your existing worker or DreamLake workflow; this package is not a scheduler.

```sh
pip install 'dreamlake-eval[pytest]'
dreamlake-eval discover --output suite.json -- tests/
pytest tests/ --eval-output reports/attempt-1 --eval-run-id experiment-1 --eval-attempt-id attempt-1
```

Collection preserves pytest node IDs, parameter IDs, markers, selected/deselected cases, and collection errors. Collection imports modules and runs collection hooks; it does not execute test bodies or set up their fixtures. Plans contain JSON data, never pickled pytest items or fixtures. Fixture scope does not automatically span remote workers.

```python
def test_quality(eval_trial):
    eval_trial.metric("reward", 0, step=0)
    eval_trial.verdict("passed", policy={"id": "quality", "version": "1"})
    eval_trial.checkpoint("s3://my-bucket/checkpoint")  # reference only
```

Every declared trial starts as `not_run`. Execution state and evaluation verdict are separate. An intermediate verdict can coexist with running execution. Fixture errors, interruptions, skips, expected failures, unexpected passes, and teardown failures remain distinguishable. Reports omit captured output, exception text, and automatic file uploads. Artifact and checkpoint references are explicit.

Each attempt exclusively creates its output directory. `events.jsonl` is flushed before atomic `eval.json` and legacy `cict.json` snapshots. A failed write stops the recorder; `dreamlake-eval replay events.jsonl --output recovered.json` rebuilds committed facts. Replay does not resume execution. Explicit retry creates a new attempt ID and higher attempt number, with `--eval-resume-from previous/eval.json --eval-prior-stopped`; stop/reconcile the previous worker first. Checkpoint restoration belongs to your workload. No arbitrary Python state is restored.

The dependency-free core exports `Recorder`, `manifest`, and `merge_reports`. Non-pytest producers supply the same serializable suite and report events explicitly. A manifest records expected jobs and full trial inventories before execution. Merge is independent of arrival order, rejects conflicting duplicates, and selects the highest attempt number and event sequence for each job. Missing jobs remain incomplete. Repeated node IDs across jobs remain separate. Metrics retain raw latest per-trial values and journal samples; the reducer does not average shard averages or invent values for missing data.

Threshold authoring and exploration belong to the DreamLake frontend. This package records explicit verdicts and policy provenance; it does not implement a threshold language or claim frontend-only rules enforce CI. The optional Dash package can consume explicit metrics and references; neither Dash nor the umbrella DreamLake SDK is a core dependency.

`dreamlake.eval/v1` is the canonical report format. `cict.json` is an explicit, lossy `dreamlake.cict/v1` projection for existing heatmaps. Existing `dreamlake.cict` imports remain supported by the umbrella SDK; installing this package does not replace those modules.

Documentation: https://eval.dreamlake.ai

## Multiple jobs and report files

```sh
dreamlake-eval manifest --evaluation-id example --job a=suite-a.json --job b=suite-b.json --output manifest.json
dreamlake-eval aggregate --manifest manifest.json --report a/eval.json --report b/eval.json --output aggregate --metric reward
```

The aggregate retains every expected trial. `complete` describes reporting coverage, not a passing evaluation verdict. An unfinished retry does not inherit an older pass. `aggregate.json` lists `views`, each pointing to a metric directory with `.dreamrc` and `cict.json`. Missing measurements remain absent; measured zero remains zero. Individual files update atomically; the output directory is not a multi-file transaction. See the runnable [CPU and async example](examples/multi_job/README.md).

Provider provenance is optional: `--source '{"provider":"dreamlake","specVersion":"v1"}'` during discovery and the matching `--eval-source` during execution. GitHub repository/ref/run identifiers can be explicit source attributes for that adapter. Inventory and evaluation reports are not executable workflow definitions; native DreamLake WorkflowSpec and GitHub Actions YAML remain owned by their respective orchestration systems.

## Workflow definition snapshots

Version 0.1.1 adds opt-in `dreamlake-eval workflow SOURCE --format yaml --provider github --revision SHA --path .github/workflows/evaluation.yml --output workflow.json`. Install the `workflow` extra for YAML-origin snapshots; native JSON snapshots remain dependency-free. Discovery accepts `--workflow workflow.json`; pytest accepts `--eval-workflow workflow.json`. Source text and parsed JSON are validated against each other before recording, retained for inspection, and bound to the inventory hash. This does not execute workflows or derive a pytest test tree from job commands. See [the complete contract](https://eval.dreamlake.ai/workflows).

## Release process

Required `package validation` checks run on supported Python/pytest versions. Reviewed exact commits merge to `main`, then the same validated commit is pushed explicitly to protected `publish-production`. The non-cancelling publish workflow builds retained immutable artifacts and refuses an already published version. Initial PyPI bootstrap uses a time-limited token in the branch-restricted `production-publish` environment; this is not a trusted-publisher/OIDC setup. No local or duplicate upload bypass is used.
