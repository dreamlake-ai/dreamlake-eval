# Pytest, async fixtures, and per-metric reports

Install `dreamlake-eval[pytest]` and `pytest-asyncio>=1,<2`, then run:

```sh
python examples/multi_job/run.py --output /tmp/my-new-evaluation
```

This CPU-only example first discovers two files and records their expected inventories. It executes them as distinct jobs, then merges their explicitly named report files. `aggregate/aggregate.json` contains completeness and per-trial outcomes; each entry in its `views` list names a directory containing a `.dreamrc` and `cict.json` for one metric. Reward zero is preserved. The duration metric is absent for the async trial, rather than filled with zero.

`pytest-asyncio` owns async fixtures and the event loop. Awaiting an async function here keeps this local pytest process alive. It is not detached remote scheduling or durable process recovery. For remote jobs, use existing orchestration to run separate invocations with stable evaluation/job IDs and new attempt IDs for retries; collect the resulting explicit files before merging. Fixture objects and event loops are never serialized.

The same operations are available as `dreamlake-eval discover`, `manifest`, and `aggregate`; see `dreamlake-eval --help` and https://eval.dreamlake.ai.

## Optional links to existing Dash runs

Copy an actual run's canonical browser URL from DreamLake Dash. Use its
`https://dreamlake.ai/NAMESPACE/dash/PROJECT/RUN` address without query/fragment;
those uppercase terms describe the route, not an existing run. Local Dash files
and a recording prefix do not establish a published run URL.

Create your own JSON file mapping exact node IDs from discovery to those copied
URLs. For this example the IDs include `test_accuracy.py::test_accuracy[seed-zero]`
and `test_async.py::test_async_trial`. Omit entries without a real destination.
An empty `{}` is a valid map and records no links. Then run:

```sh
python examples/multi_job/run.py --output /tmp/new-linked-evaluation --dash-links /absolute/path/to/your-dash-links.json
```

The example-only `conftest.py` fixture calls
`eval_trial.artifact(actual_dash_run_url, name="Dash run")` for each explicitly
mapped test, including async tests. This is not a new framework option or an
automatic run finder. It creates no remote workload, uploads nothing, and makes
no claim that the destination exists or that the current reader can access it.
`aggregate.json` retains the selected attempt's references at
`rows[].attempt.artifacts`; use the canonical report, not the lossy `cict.json`
projection. A fresh retry must explicitly record its own link.
