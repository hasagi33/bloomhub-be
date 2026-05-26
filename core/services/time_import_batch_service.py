from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from core.enums import ImportBatchSource, ImportBatchStatus, ImportRowStatus
from core.models import TimeEntry, TimeImportBatch, TimeImportRow
from core.services.time_tracking_service import profile_for_user


def _filters_payload(filters) -> dict[str, Any]:
    if is_dataclass(filters):
        payload = asdict(filters)
    else:
        payload = dict(filters or {})
    return {
        key: (value.isoformat() if hasattr(value, "isoformat") else value)
        for key, value in payload.items()
    }


def persist_external_import_batch(
    *,
    user,
    source_type: ImportBatchSource,
    filters,
    preview: dict[str, Any],
    counts: dict[str, int],
    entry_ids_by_worklog: dict[str, int],
) -> TimeImportBatch:
    error_count = counts.get("error", 0)
    committed_count = counts.get("created", 0) + counts.get("updated", 0)
    skipped_count = counts.get("skipped", 0)
    batch = TimeImportBatch.objects.create(
        source_type=source_type,
        uploaded_by=profile_for_user(user),
        requested_filters=_filters_payload(filters),
        status=(
            ImportBatchStatus.PARTIALLY_COMMITTED
            if error_count
            else ImportBatchStatus.COMMITTED
        ),
        total_rows=preview.get("row_count", len(preview.get("rows", []))),
        valid_rows=preview.get("valid_count", 0),
        error_rows=error_count,
        skipped_rows=skipped_count,
        committed_rows=committed_count,
        validation_messages=(
            [
                {
                    "code": "import_errors",
                    "message": f"{error_count} rows failed validation.",
                }
            ]
            if error_count
            else []
        ),
    )
    entries = {
        entry.source_external_id: entry
        for entry in TimeEntry.objects.filter(id__in=entry_ids_by_worklog.values())
    }
    rows = []
    for index, row in enumerate(preview.get("rows", []), start=1):
        worklog_id = row.get("worklog_id", "")
        if row.get("status") == "error":
            row_status = ImportRowStatus.ERROR
        elif row.get("action") == "skip":
            row_status = ImportRowStatus.SKIPPED
        else:
            row_status = ImportRowStatus.COMMITTED
        rows.append(
            TimeImportRow(
                batch=batch,
                row_number=index,
                row_index=index - 1,
                raw_data={"worklog_id": worklog_id},
                parsed_data=row,
                original_row_fingerprint=row.get("duplicate_fingerprint") or worklog_id,
                status=row_status,
                validation_messages=row.get("validation_messages", []),
                committed_entry=entries.get(worklog_id),
            )
        )
    TimeImportRow.objects.bulk_create(rows)
    return batch
