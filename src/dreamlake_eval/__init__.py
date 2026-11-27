"""Explicit evaluation recording. Importing this module does not load pytest."""
from .model import SCHEMA_VERSION, Recorder, Trial, read_report, replay, export_cict
from .aggregate import manifest, merge_reports, write_aggregate

__all__ = ["SCHEMA_VERSION", "Recorder", "Trial", "read_report", "replay", "export_cict", "manifest", "merge_reports", "write_aggregate"]
__version__ = "0.1.0"
