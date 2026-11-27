"""Pure multi-job report reduction. No scheduling, inferred jobs, or averages."""
from copy import deepcopy
import hashlib

from .model import SCHEMA_VERSION, definition_hash, encoded, json_value, text, validate_suite, validate_report, validate_progress


def manifest(evaluation_id: str, jobs: dict[str, dict]) -> dict:
    """Declare expected job inventories before accepting reports.

    Job IDs distinguish machines/shards even when pytest node IDs are identical.
    An incomplete collection cannot establish a complete evaluation inventory.
    """
    if not isinstance(jobs, dict) or not jobs:
        raise ValueError("manifest requires at least one explicitly named job")
    entries = []
    for job_id, suite in jobs.items():
        suite = validate_suite(suite)
        entries.append({"jobId": text(job_id), "definitionHash": definition_hash(suite), "suite": suite})
    return {"schemaVersion": SCHEMA_VERSION, "kind": "manifest", "evaluationId": text(evaluation_id),
            "jobs": sorted(entries, key=lambda job: job["jobId"])}


def merge_reports(plan: dict, reports: list[dict]) -> dict:
    """Order-independent reduction with explicit completeness and retry identity.

    Highest explicit attemptNumber wins, including unfinished retries. Within
    an attempt the highest sequence wins. Conflicting repeated identities fail;
    an old pass never fills in a missing new attempt. Raw latest measurements
    remain per trial. Statistical aggregation requires its own declared policy.
    """
    plan = json_value(plan)
    if not isinstance(plan, dict) or plan.get("schemaVersion") != SCHEMA_VERSION or plan.get("kind") != "manifest":
        raise ValueError("expected an evaluation manifest")
    text(plan.get("evaluationId"))
    if not isinstance(plan.get("jobs"), list):
        raise ValueError("manifest requires jobs")
    for job in plan["jobs"]:
        text(job.get("jobId"))
        if definition_hash(validate_suite(job.get("suite"))) != job.get("definitionHash"):
            raise ValueError("manifest inventory does not match its definition hash")
    jobs = {job["jobId"]: job for job in plan["jobs"]}
    if len(jobs) != len(plan["jobs"]) or not jobs:
        raise ValueError("duplicate or empty job inventory")
    versions, attempt_ids, accepted, timelines, reused_ids = {}, {}, {}, {}, {}
    for original in reports:
        report = json_value(original)
        if report.get("schemaVersion") != SCHEMA_VERSION or report.get("kind") != "run" or report.get("evaluationId") != plan["evaluationId"]:
            raise ValueError("report belongs to another schema or evaluation")
        job_id = report.get("jobId")
        if job_id not in jobs or report.get("definitionHash") != jobs[job_id]["definitionHash"]:
            raise ValueError("unexpected job or changed inventory/context")
        number, seq = report.get("attemptNumber"), report.get("sequence")
        if type(number) is not int or number < 1 or type(seq) is not int or seq < 1:
            raise ValueError("invalid attempt number or event sequence")
        attempt_id = text(report.get("attemptId"))
        identity = (job_id, number)
        if identity in attempt_ids and attempt_ids[identity] != attempt_id:
            raise ValueError("conflicting attempt identity for the same number")
        attempt_ids[identity] = attempt_id
        reuse = (job_id, attempt_id)
        if reuse in reused_ids and reused_ids[reuse] != number:
            raise ValueError("attempt ID reused under another number")
        reused_ids[reuse] = number
        key = (*identity, seq)
        digest = hashlib.sha256(encoded(report)).hexdigest()
        if key in versions and versions[key] != digest:
            raise ValueError("conflicting report replay")
        versions[key] = digest
        validate_report(report)
        timelines.setdefault(identity, {})[seq] = report
        expected = {t["id"] for t in jobs[job_id]["suite"]["trials"]}
        found = [t["id"] for t in report.get("trials", [])]
        if set(found) != expected or len(found) != len(expected):
            raise ValueError("report must retain the full declared trial inventory")
        for trial in report["trials"]:
            if trial.get("attempt", {}).get("id") != attempt_id:
                raise ValueError("mixed trial attempt identities")
        prior = accepted.get(job_id)
        if prior is None or (number, seq) > (prior["attemptNumber"], prior["sequence"]):
            accepted[job_id] = report
    for timeline in timelines.values():
        ordered = [timeline[seq] for seq in sorted(timeline)]
        for previous, current in zip(ordered, ordered[1:]):
            validate_progress(previous, current)
    rows, job_states = [], []
    for job_id, job in jobs.items():
        report = accepted.get(job_id)
        actual = {t["id"]: t for t in report["trials"]} if report else {}
        complete = bool(report and report.get("finishedAt") and report.get("execution") != "running"
                        and job["suite"]["collection"].get("complete") is True
                        and report["collection"].get("complete") is True
                        and all(actual[t["id"]]["attempt"]["execution"] not in {"not_run", "running", "unknown", "interrupted"}
                                for t in job["suite"]["trials"] if t["selection"] == "selected"))
        job_states.append({"jobId": job_id, "complete": complete,
                           "availability": "reported" if report else "missing",
                           "attemptId": report["attemptId"] if report else None,
                           "attemptNumber": report["attemptNumber"] if report else None,
                           "execution": report.get("execution") if report else "unknown"})
        if "workflow" in job["suite"]:
            job_states[-1]["workflow"] = deepcopy(job["suite"]["workflow"])
        for expected in job["suite"]["trials"]:
            trial = actual.get(expected["id"])
            attempt = trial["attempt"] if trial else None
            rows.append({"id": encoded([job_id, expected["id"]]).decode(), "jobId": job_id,
                         "trialId": expected["id"], "nodeId": expected["nodeId"],
                         "caseId": expected["caseId"], "context": deepcopy(expected.get("context", {})),
                         "selection": trial["selection"] if trial else expected["selection"],
                         "availability": "reported" if trial else "missing",
                         "attempt": deepcopy(attempt)})
    return {"schemaVersion": SCHEMA_VERSION, "kind": "aggregate", "evaluationId": plan["evaluationId"],
            "complete": all(job["complete"] for job in job_states), "jobs": job_states, "rows": rows}


def write_aggregate(plan: dict, report_paths: list, output, *, metrics: list[str]) -> dict:
    """Read explicitly named files and write an aggregate plus per-metric views.

    No directory scanning, log capture, numeric reduction or uploads. Each JSON
    file is atomic; the directory is not a multi-file transaction.
    """
    from pathlib import Path
    from .model import atomic_write, read_report, export_cict
    result = merge_reports(plan, [read_report(path) for path in report_paths])
    metric_names = sorted({text(metric) for metric in metrics})
    records = []
    for row in result["rows"]:
        attempt = row["attempt"]
        if attempt:
            projection = export_cict({"suiteId": "aggregate", "runId": result["evaluationId"], "trials": [{
                "id": row["id"], "nodeId": row["nodeId"], "caseId": row["caseId"],
                "selection": row["selection"], "attempt": attempt}]})["records"][0]
        else:
            projection = {"id": row["id"], "testId": row["nodeId"], "name": row["nodeId"],
                          "kind": "test", "status": "unknown", "metrics": {}, "attributes": {}}
        projection["attributes"].update(jobId=row["jobId"], availability=row["availability"],
                                        context=deepcopy(row["context"]))
        records.append(projection)
    output = Path(output)
    views = []
    for metric in metric_names:
        # Path identity is derived, never an untrusted metric name interpreted as a path.
        directory = "metric-" + hashlib.sha256(metric.encode()).hexdigest()[:16]
        data = {"schemaVersion": "dreamlake.cict/v1", "kind": "run", "runId": result["evaluationId"],
                "suiteId": result["evaluationId"], "name": metric, "records": deepcopy(records), "edges": []}
        config = {"version": 1, "dataset": {"format": "cict", "file": "cict.json"}, "views": [{
                  "view": "activityHeatmap", "records": ["records"], "row": {"field": "attributes.jobId"},
                  "column": {"field": "testId"}, "value": "metrics.value", "status": "status"}]}
        for record in data["records"]:
            raw = record["metrics"]
            record["metrics"] = {"value": raw[metric]} if metric in raw else {}
            record["attributes"]["metric"] = metric
        atomic_write(output / directory / "cict.json", data)
        atomic_write(output / directory / ".dreamrc", config)
        views.append({"metric": metric, "directory": directory})
    atomic_write(output / "cict.json", {"schemaVersion": "dreamlake.cict/v1", "kind": "run",
                 "runId": result["evaluationId"], "suiteId": result["evaluationId"], "name": "Evaluation inventory", "records": records, "edges": []})
    atomic_write(output / ".dreamrc", {"version": 1, "dataset": {"format": "cict", "file": "cict.json"},
                 "views": [{"view": "activityHeatmap", "records": ["records"],
                            "row": {"field": "attributes.jobId"}, "column": {"field": "testId"}, "status": "status"}]})
    result["views"] = views
    atomic_write(output / "aggregate.json", result)
    return result
