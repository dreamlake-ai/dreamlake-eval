# Pytest, async fixtures, and per-metric reports

Install `dreamlake-eval[pytest]` and `pytest-asyncio>=1,<2`, then run:

```sh
python examples/multi_job/run.py --output /tmp/my-new-evaluation
```

This CPU-only example first discovers two files and records their expected inventories. It executes them as distinct jobs, then merges their explicitly named report files. `aggregate/aggregate.json` contains completeness and per-trial outcomes; each entry in its `views` list names a directory containing a `.dreamrc` and `cict.json` for one metric. Reward zero is preserved. The duration metric is absent for the async trial, rather than filled with zero.

`pytest-asyncio` owns async fixtures and the event loop. Awaiting an async function here keeps this local pytest process alive. It is not detached remote scheduling or durable process recovery. For remote jobs, use existing orchestration to run separate invocations with stable evaluation/job IDs and new attempt IDs for retries; collect the resulting explicit files before merging. Fixture objects and event loops are never serialized.

The same operations are available as `dreamlake-eval discover`, `manifest`, and `aggregate`; see `dreamlake-eval --help` and https://eval.dreamlake.ai.
