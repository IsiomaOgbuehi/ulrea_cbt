"""
cbt_service/services/item/bulk_upload_service.py

Bulk question import. Accepts:
  1. Our native template (questions_template.xlsx)
  2. Edu Expression Pro exports (SampleQuestion.xls layout)

Flow:
  read file -> detect layout from HEADERS -> (convert foreign rows into our
  row shape) -> shared _validate_row -> insert valid rows -> log the upload.

Adding another platform later = one signature set + one _map_<platform>_row
function + one branch in process_upload. Nothing else changes.

Requires: openpyxl (already used), xlrd (NEW - needed for legacy .xls)
"""
from uuid import UUID, uuid4
import io

import openpyxl
import xlrd
from fastapi import HTTPException, UploadFile
from sqlmodel import Session

from cbt_service.database.models.item import ItemModel, BulkUploadLog
from cbt_service.database.models.enums.item_subject_enums import ItemType, ItemSource
from cbt_service.schemas.item_subject_schemas import BulkUploadResult, CurrentUser
from cbt_service.services.item.item_service import ItemService
from cbt_service.services.item.utils.item_answer_utils import (
    assert_correct_answers_valid,
    build_true_false_options,
    normalize_boolean_answer,
    normalize_correct_answers,
)


# ============================================================
# CONSTANTS
# ============================================================

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5MB

# --- native template ---
REQUIRED_COLUMNS = {"question_text", "item_type"}

OPTIONAL_COLUMNS = {
    "option_a", "option_b", "option_c", "option_d", "option_e",
    "correct_answers", "explanation", "marks", "negative_marks",
    "tags", "difficulty",
}

VALID_TYPES = {t.value for t in ItemType}
VALID_DIFFICULTIES = {"easy", "medium", "hard"}

# --- Edu Expression layout (headers are matched lowercased, because the
#     source mixes "Option1" and "option2") ---
EDU_SIGNATURE = {"question", "question type", "difficulty level"}
EDU_DIFFICULTY_MAP = {"e": "easy", "m": "medium", "h": "hard"}
EDU_OPTION_COLS = ["option1", "option2", "option3", "option4", "option5", "option6"]
OPTION_LETTERS = ["A", "B", "C", "D", "E"]  # our schema supports 5 options


# ============================================================
# SAFE UTIL
# ============================================================

def _safe_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


# ============================================================
# UPLOAD SIZE LIMIT
# ============================================================

async def read_upload_within_limit(file: UploadFile, max_bytes: int = MAX_UPLOAD_BYTES) -> bytes:
    """Reads in 1MB chunks and rejects as soon as the cap is crossed, instead of
    buffering an oversized file fully into memory before checking."""
    chunks = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds maximum size of {max_bytes // (1024 * 1024)}MB.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


# ============================================================
# FILE LOADING + FORMAT DETECTION
# ============================================================

def _load_grid(file_bytes: bytes) -> list[list]:
    """
    Reads .xlsx or legacy .xls into a plain list of rows.
    The reader is chosen from the file's leading bytes, NOT the filename, so a
    renamed or re-saved file still works.
    Also normalizes integral floats (xlrd returns 2 as 2.0) to ints.
    """
    try:
        if file_bytes[:4] == b"PK\x03\x04":  # .xlsx (zip container)
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
            raw = [list(r) for r in wb.active.iter_rows(values_only=True)]
        elif file_bytes[:4] == b"\xd0\xcf\x11\xe0":  # legacy .xls (OLE2)
            sheet = xlrd.open_workbook(file_contents=file_bytes).sheet_by_index(0)
            raw = [sheet.row_values(i) for i in range(sheet.nrows)]
        else:
            raise ValueError("not an Excel file")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or unreadable Excel file.")

    def clean(v):
        return int(v) if isinstance(v, float) and v.is_integer() else v

    return [[clean(v) for v in row] for row in raw]


def _detect_source_format(headers: list[str]) -> str:
    """Layout is decided by column headers only."""
    header_set = set(headers)
    if REQUIRED_COLUMNS <= header_set:
        return "native"
    if EDU_SIGNATURE <= header_set:
        return "edu_expression"
    raise HTTPException(
        status_code=400,
        detail=(
            "Unrecognized file layout. Use the template from GET /bulk/template, "
            "or upload a supported export (Edu Expression)."
        ),
    )


# ============================================================
# NATIVE TEMPLATE PARSERS
# ============================================================

def _parse_options(row: dict) -> list[dict] | None:
    """Returns JSON-safe options: [{"key": "A", "text": "Bike"}, ...]"""
    mapping = [
        ("A", "option_a"),
        ("B", "option_b"),
        ("C", "option_c"),
        ("D", "option_d"),
        ("E", "option_e"),
    ]
    options = []
    for key, col in mapping:
        val = _safe_str(row.get(col)).strip()
        if val:
            options.append({"key": key, "text": val})
    return options or None


def _parse_correct_answers(raw: str) -> list[str] | None:
    raw = _safe_str(raw).strip()
    if not raw:
        return None
    return [x.strip() for x in raw.split(",") if x.strip()]


def _parse_tags(raw: str) -> list[str] | None:
    raw = _safe_str(raw).strip()
    if not raw:
        return None
    return [t.strip() for t in raw.split(",") if t.strip()]


# ============================================================
# EDU EXPRESSION -> NATIVE ROW CONVERSION
# ============================================================

def _map_edu_expression_row(get) -> dict:
    """
    Converts one Edu Expression row into the same row-dict shape the native
    template produces, so _validate_row (and everything after it) is shared.
    `get(name)` returns the cell for a lowercased header, or None.
    Raises ValueError with a reason if the row can't be converted.

    Mapping:
      Difficulty Level  E/M/H           -> easy/medium/hard
      Question Type     M               -> mcq_single (mcq_multi if Correct Answer has a comma)
                        T               -> true_false
                        F (fill blank)  -> short_answer, expected text kept in correct_answers
                                           (manual review; NOT auto-graded)
                        S (subjective)  -> short_answer, no answer key
      Correct Answer    1-based index   -> option letter A-E
      Option6 with a value              -> row rejected (we support 5 options)
      Hint                              -> prepended to explanation
    """
    q_type = _safe_str(get("question type")).strip().upper()
    difficulty = EDU_DIFFICULTY_MAP.get(_safe_str(get("difficulty level")).strip().lower(), "")

    marks = get("marks")
    negative_marks = get("negative marks")

    explanation = _safe_str(get("explanation")).strip()
    hint = _safe_str(get("hint")).strip()
    if hint:
        explanation = f"Hint: {hint}" + (f"\n\n{explanation}" if explanation else "")

    row = {
        "question_text": _safe_str(get("question")).strip(),
        "explanation": explanation,
        "marks": _safe_str(marks) if marks not in (None, "") else "",
        "negative_marks": _safe_str(negative_marks) if negative_marks not in (None, "") else "",
        "tags": "",
        "difficulty": difficulty,
        "option_a": "", "option_b": "", "option_c": "", "option_d": "", "option_e": "",
        "correct_answers": "",
    }

    if q_type == "M":
        option_values = [_safe_str(get(col)).strip() for col in EDU_OPTION_COLS]

        if option_values[5]:
            raise ValueError(
                f"uses a 6th option ('{option_values[5]}') - only 5 options "
                f"(option_a to option_e) are supported"
            )

        for letter, val in zip(OPTION_LETTERS, option_values[:5]):
            row[f"option_{letter.lower()}"] = val

        correct_raw = _safe_str(get("correct answer")).strip()
        letters = []
        for idx in [x.strip() for x in correct_raw.split(",") if x.strip()]:
            try:
                n = int(idx)
            except ValueError:
                raise ValueError(f"non-numeric Correct Answer '{idx}'")
            if not (1 <= n <= 5):
                raise ValueError(f"Correct Answer index {n} is outside the supported range (1-5)")
            letters.append(OPTION_LETTERS[n - 1])

        row["item_type"] = "mcq_multi" if len(letters) > 1 else "mcq_single"
        row["correct_answers"] = ",".join(letters)

    elif q_type == "T":
        row["item_type"] = "true_false"
        # _validate_row ignores option_a/b for true_false and always uses
        # build_true_false_options(), so nothing to populate here.
        # NOTE: Excel stores this as a boolean cell; xlrd returns it as 1/0,
        # openpyxl as True/False, and some exports use text - handle all,
        # and reject blanks rather than silently defaulting to False.
        tf = _safe_str(get("true & false")).strip().lower()
        if tf in ("true", "1", "t", "yes"):
            row["correct_answers"] = "True"
        elif tf in ("false", "0", "f", "no"):
            row["correct_answers"] = "False"
        else:
            raise ValueError(f"unreadable True & False value '{tf}'")

    elif q_type == "F":
        row["item_type"] = "short_answer"
        row["correct_answers"] = _safe_str(get("fill in the blanks")).strip()

    elif q_type == "S":
        row["item_type"] = "short_answer"
        row["correct_answers"] = ""

    else:
        raise ValueError(f"unrecognized Question Type '{q_type}'")

    return row


def _extract_rows_edu_expression(headers: list[str], data_rows: list[list]):
    """Yields (row_num, row_dict, error) tuples; exactly one of row_dict/error is set."""
    col = {h: i for i, h in enumerate(headers) if h}
    results = []
    for row_num, r in enumerate(data_rows, start=2):
        get = lambda name: r[col[name]] if name in col and col[name] < len(r) else None

        if not get("question"):
            continue  # blank row

        try:
            results.append((row_num, _map_edu_expression_row(get), None))
        except ValueError as e:
            results.append((row_num, None, f"Row {row_num}: {e}"))
    return results


# ============================================================
# VALIDATION (shared by every format)
# ============================================================

def _validate_row(row: dict, row_num: int):
    question_text = _safe_str(row.get("question_text")).strip()
    if not question_text:
        return None, f"Row {row_num}: question_text is required"

    raw_type = _safe_str(row.get("item_type")).strip().lower()
    if raw_type not in VALID_TYPES:
        return None, f"Row {row_num}: invalid item_type '{raw_type}'"

    if raw_type == ItemType.TRUE_FALSE.value:
        options = build_true_false_options()  # ignore option_a/option_b, always canonical
    else:
        options = _parse_options(row)

    raw_correct = row.get("correct_answers") or row.get("correct_answer")
    correct_answers = _parse_correct_answers(raw_correct)

    if raw_type == ItemType.TRUE_FALSE.value:
        correct_answers = normalize_boolean_answer(correct_answers)
    else:
        correct_answers = normalize_correct_answers(options, correct_answers)

    difficulty = _safe_str(row.get("difficulty")).strip().lower() or None
    if difficulty and difficulty not in VALID_DIFFICULTIES:
        return None, f"Row {row_num}: invalid difficulty '{difficulty}'"

    try:
        marks = float(row.get("marks") or 1.0)
        negative_marks = float(row.get("negative_marks") or 0.0)
    except Exception:
        return None, f"Row {row_num}: marks must be numeric"

    if raw_type in (ItemType.MCQ_SINGLE.value, ItemType.MCQ_MULTI.value, ItemType.TRUE_FALSE.value):
        if not options or len(options) < 2:
            return None, f"Row {row_num}: MCQ/TF requires at least 2 options"
        if not correct_answers:
            return None, f"Row {row_num}: correct_answers required"

        try:
            assert_correct_answers_valid(options, correct_answers)
        except HTTPException as exc:
            return None, f"Row {row_num}: {exc.detail}"

        valid_keys = {opt["key"] for opt in options}
        bad = [a for a in correct_answers if a not in valid_keys]
        if bad:
            return None, (
                f"Row {row_num}: correct_answers {bad} doesn't match any option "
                f"key or text (valid keys: {', '.join(sorted(valid_keys))})"
            )

    if raw_type == ItemType.NUMERIC.value and not correct_answers:
        return None, f"Row {row_num}: numeric requires correct_answers"

    return {
        "question_text": question_text,
        "item_type": raw_type,
        "options": options,
        "correct_answers": correct_answers,
        "explanation": _safe_str(row.get("explanation")).strip() or None,
        "marks": marks,
        "negative_marks": negative_marks,
        "tags": _parse_tags(row.get("tags")),
        "difficulty": difficulty,
    }, None


# ============================================================
# SERVICE
# ============================================================

class BulkUploadService:

    # --------------------------------------------------------
    # TEMPLATE
    # --------------------------------------------------------
    @staticmethod
    def generate_template() -> bytes:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Questions"

        headers = [
            "question_text", "item_type",
            "option_a", "option_b", "option_c", "option_d", "option_e",
            "correct_answers", "explanation",
            "marks", "negative_marks", "tags", "difficulty",
        ]
        ws.append(headers)

        examples = [
            ["What is 2+2?", "mcq_single", "3", "4", "5", "6", "", "B", "Basic math", 1, 0, "math", "easy"],
            ["Prime numbers?", "mcq_multi", "2", "3", "4", "5", "", "A,B,D", "", 2, 0.5, "math", "medium"],
            ["Sky is blue", "true_false", "True", "False", "", "", "", "True", "", 1, 0, "general", "easy"],
            ["Speed of light?", "numeric", "", "", "", "", "", "299792458", "", 2, 0, "physics", "hard"],
            ["Describe photosynthesis", "short_answer", "", "", "", "", "", "", "Manual review", 5, 0, "biology", "medium"],
        ]
        for r in examples:
            ws.append(r)

        ws_info = wb.create_sheet("Instructions")
        instructions = [
            ["Column", "Required", "Description"],
            ["question_text", "Yes", "The question text"],
            ["item_type", "Yes", "mcq_single | mcq_multi | true_false | numeric | short_answer"],
            ["option_a to option_e", "For MCQ/TF", "Answer options (at least 2 for MCQ)"],
            ["correct_answers", "For most types", "Option key (A, B...). Multi: comma-separated (A,C). Numeric: the number."],
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

    # --------------------------------------------------------
    # PROCESS UPLOAD
    # --------------------------------------------------------
    @staticmethod
    def process_upload(
        session: Session,
        file_bytes: bytes,
        filename: str,
        subject_id: UUID,
        current_user: CurrentUser,
    ) -> BulkUploadResult:

        # Same guard every other item endpoint uses: subject must exist in the
        # caller's org, and teachers must be assigned to it.
        ItemService._assert_subject_access(session, subject_id, current_user)

        grid = _load_grid(file_bytes)
        if len(grid) < 2:
            raise HTTPException(status_code=400, detail="Empty file")

        headers = [str(h).strip().lower() if h not in (None, "") else "" for h in grid[0]]
        fmt = _detect_source_format(headers)

        data_rows = grid[1:]
        total_rows = len(data_rows)

        # Normalize both layouts into (row_num, row_dict, pre_error) tuples
        if fmt == "native":
            extracted = []
            for row_num, row_data in enumerate(data_rows, start=2):
                row = {k: (str(v).strip() if v is not None else "") for k, v in zip(headers, row_data)}
                if not any(row.values()):
                    continue
                extracted.append((row_num, row, None))
        else:  # edu_expression
            extracted = _extract_rows_edu_expression(headers, data_rows)

        upload_id = uuid4()
        successful = []
        errors = []

        for row_num, row, pre_error in extracted:
            if pre_error:
                errors.append({"row": row_num, "error": pre_error})
                continue

            cleaned, error = _validate_row(row, row_num)
            if error:
                errors.append({"row": row_num, "error": error})
                continue

            item = ItemModel(
                org_id=current_user.org_id,
                subject_id=subject_id,
                created_by=current_user.id,
                source=ItemSource.EXCEL_UPLOAD,
                bulk_upload_id=upload_id,
                **cleaned,
            )
            session.add(item)
            successful.append(item)

        if successful:
            session.commit()

        log = BulkUploadLog(
            id=upload_id,
            org_id=current_user.org_id,
            subject_id=subject_id,
            uploaded_by=current_user.id,
            filename=filename,
            total_rows=total_rows,
            successful_rows=len(successful),
            failed_rows=len(errors),
            errors=errors,
        )
        session.add(log)
        session.commit()

        return BulkUploadResult(
            total_rows=total_rows,
            successful_rows=len(successful),
            failed_rows=len(errors),
            errors=errors,
            upload_id=upload_id,
        )