from uuid import UUID, uuid4
from datetime import datetime, timezone
import io
import openpyxl
from fastapi import HTTPException
from sqlmodel import Session

from item_bank_service.database.models.item import ItemModel, BulkUploadLog
from item_bank_service.database.models.enums import ItemType
from item_bank_service.schemas.schemas import BulkUploadResult, CurrentUser


# Expected Excel columns (case-insensitive, order-independent)
REQUIRED_COLUMNS = {"stem", "type"}
OPTIONAL_COLUMNS = {
    "option_a", "option_b", "option_c", "option_d", "option_e",
    "correct_answer", "explanation", "marks", "negative_marks",
    "tags", "difficulty"
}
ALL_COLUMNS = REQUIRED_COLUMNS | OPTIONAL_COLUMNS

VALID_TYPES = {t.value for t in ItemType}
VALID_DIFFICULTIES = {"easy", "medium", "hard"}


def _parse_options(row: dict) -> list[str] | None:
    options = []
    for key in ["option_a", "option_b", "option_c", "option_d", "option_e"]:
        val = row.get(key, "").strip()
        if val:
            options.append(val)
    return options if options else None


def _parse_correct_answer(raw: str, item_type: str) -> list[str] | None:
    if not raw:
        return None
    # Support comma-separated for multi: "A,C" or "A, C"
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts if parts else None


def _parse_tags(raw: str) -> list[str] | None:
    if not raw:
        return None
    return [t.strip() for t in raw.split(",") if t.strip()]


def _validate_row(row: dict, row_num: int) -> tuple[dict | None, str | None]:
    """Returns (cleaned_row, error_message). One or the other will be None."""

    stem = str(row.get("stem", "")).strip()
    if not stem:
        return None, f"Row {row_num}: 'stem' is required."

    raw_type = str(row.get("type", "")).strip().lower()
    if raw_type not in VALID_TYPES:
        return None, f"Row {row_num}: invalid type '{raw_type}'. Must be one of: {', '.join(VALID_TYPES)}."

    options = _parse_options(row)
    raw_correct = str(row.get("correct_answer", "")).strip()
    correct_answer = _parse_correct_answer(raw_correct, raw_type)

    # MCQ/True-False require options and a correct answer
    if raw_type in (ItemType.MCQ_SINGLE, ItemType.MCQ_MULTI, ItemType.TRUE_FALSE):
        if not options or len(options) < 2:
            return None, f"Row {row_num}: '{raw_type}' requires at least 2 options (option_a, option_b...)."
        if not correct_answer:
            return None, f"Row {row_num}: 'correct_answer' is required for type '{raw_type}'."

    if raw_type == ItemType.NUMERIC and not correct_answer:
        return None, f"Row {row_num}: 'correct_answer' is required for numeric type."

    # Validate difficulty
    difficulty = str(row.get("difficulty", "")).strip().lower() or None
    if difficulty and difficulty not in VALID_DIFFICULTIES:
        return None, f"Row {row_num}: invalid difficulty '{difficulty}'. Must be: easy, medium, hard."

    try:
        marks = float(row.get("marks") or 1.0)
        negative_marks = float(row.get("negative_marks") or 0.0)
    except (ValueError, TypeError):
        return None, f"Row {row_num}: 'marks' and 'negative_marks' must be numbers."

    return {
        "stem": stem,
        "type": raw_type,
        "options": options,
        "correct_answer": correct_answer,
        "explanation": str(row.get("explanation", "")).strip() or None,
        "marks": marks,
        "negative_marks": negative_marks,
        "tags": _parse_tags(str(row.get("tags", ""))),
        "difficulty": difficulty,
    }, None


class BulkUploadService:

    @staticmethod
    def generate_template() -> bytes:
        """Returns an Excel template file as bytes."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Questions"

        headers = [
            "stem", "type",
            "option_a", "option_b", "option_c", "option_d", "option_e",
            "correct_answer", "explanation",
            "marks", "negative_marks", "tags", "difficulty"
        ]

        # Header row with styling
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = openpyxl.styles.Font(bold=True)
            cell.fill = openpyxl.styles.PatternFill("solid", fgColor="4A90E2")
            cell.font = openpyxl.styles.Font(bold=True, color="FFFFFF")

        # Example rows
        examples = [
            ["What is 2 + 2?", "mcq_single", "3", "4", "5", "6", "", "B", "Basic arithmetic.", 1, 0, "math,arithmetic", "easy"],
            ["Which are prime numbers?", "mcq_multi", "2", "3", "4", "5", "", "A,B,D", "Primes: 2,3,5", 2, 0.5, "math", "medium"],
            ["The sky is blue.", "true_false", "True", "False", "", "", "", "True", "", 1, 0, "general", "easy"],
            ["What is the speed of light (m/s)?", "numeric", "", "", "", "", "", "299792458", "", 2, 0, "physics", "hard"],
            ["Describe photosynthesis.", "short_answer", "", "", "", "", "", "", "Manual review required.", 5, 0, "biology", "medium"],
        ]

        for row_data in examples:
            ws.append(row_data)

        # Instructions sheet
        ws_info = wb.create_sheet("Instructions")
        instructions = [
            ["Column", "Required", "Description"],
            ["stem", "Yes", "The question text"],
            ["type", "Yes", "mcq_single | mcq_multi | true_false | numeric | short_answer"],
            ["option_a to option_e", "For MCQ/TF", "Answer options (at least 2 for MCQ)"],
            ["correct_answer", "For most types", "For MCQ: the option label (A, B...). Multi: comma-separated (A,C). Numeric: the number."],
            ["explanation", "No", "Shown to candidate after exam"],
            ["marks", "No", "Points awarded. Default: 1"],
            ["negative_marks", "No", "Points deducted for wrong answer. Default: 0"],
            ["tags", "No", "Comma-separated tags e.g. algebra,chapter-3"],
            ["difficulty", "No", "easy | medium | hard"],
        ]
        for row in instructions:
            ws_info.append(row)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return output.read()

    @staticmethod
    def process_upload(
        session: Session,
        file_bytes: bytes,
        filename: str,
        subject_id: UUID,
        current_user: CurrentUser,
    ) -> BulkUploadResult:

        # Parse workbook
        try:
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid Excel file. Please use the provided template.")

        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))

        if len(rows) < 2:
            raise HTTPException(status_code=400, detail="File is empty or missing data rows.")

        # Normalize headers
        raw_headers = [str(h).strip().lower() if h else "" for h in rows[0]]
        missing = REQUIRED_COLUMNS - set(raw_headers)
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required columns: {', '.join(missing)}. Please use the provided template."
            )

        upload_id = uuid4()
        successful = []
        errors = []

        for row_num, row_data in enumerate(rows[1:], start=2):
            row = dict(zip(raw_headers, [str(v).strip() if v is not None else "" for v in row_data]))

            # Skip completely empty rows
            if not any(row.values()):
                continue

            cleaned, error = _validate_row(row, row_num)

            if error:
                errors.append({"row": row_num, "error": error})
                continue

            item = ItemModel(
                org_id=current_user.org_id,
                subject_id=subject_id,
                created_by=current_user.id,
                source="excel_upload",
                bulk_upload_id=upload_id,
                **cleaned,
            )
            session.add(item)
            successful.append(item)

        # Commit all successful rows together
        if successful:
            session.commit()

        # Log the upload
        log = BulkUploadLog(
            id=upload_id,
            org_id=current_user.org_id,
            subject_id=subject_id,
            uploaded_by=current_user.id,
            filename=filename,
            total_rows=len(rows) - 1,
            successful_rows=len(successful),
            failed_rows=len(errors),
            errors=errors,
        )
        session.add(log)
        session.commit()

        return BulkUploadResult(
            total_rows=len(rows) - 1,
            successful_rows=len(successful),
            failed_rows=len(errors),
            errors=errors,
            upload_id=upload_id,
        )
