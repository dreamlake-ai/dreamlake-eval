"""Explicit evaluation recording. Importing this module does not load pytest."""
from .model import SCHEMA_VERSION, Recorder, Trial, read_report, replay, export_cict
from .workflow import WORKFLOW_SCHEMA_VERSION, workflow_snapshot, validate_workflow, read_workflow, write_workflow
from .aggregate import manifest, merge_reports, write_aggregate

__all__ = ["SCHEMA_VERSION", "Recorder", "Trial", "read_report", "replay", "export_cict", "manifest", "merge_reports", "write_aggregate", "WORKFLOW_SCHEMA_VERSION", "workflow_snapshot", "validate_workflow", "read_workflow", "write_workflow"]
__version__ = "0.1.1"
