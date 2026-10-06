"""
cbt_service/tests/test_bulk_import.py

Covers the bulk-upload changes: Edu Expression layout support, content-based
format detection, the 5MB limit, and the new error paths.

Your existing native-template tests (in the items test module) are unchanged
and still apply.

Optional: copy SampleQuestion.xls to cbt_service/tests/fixtures/ to enable the
real-file smoke test (it is skipped if the file isn't there).
"""
import io
from pathlib import Path

import openpyxl
import pytest

from .conftest import create_subject, assign_teacher, TEACHER_ID

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLS = "application/vnd.ms-excel"

NATIVE_HEADERS = [
    "question_text", "item_type", "option_a", "option_b", "option_c", "option_d",
    "correct_answers", "explanation", "marks", "negative_marks", "tags", "difficulty",
]

# Same header casing quirks as the real export (note "Option1" vs "option2")
EDU_HEADERS = [
    "Difficulty Level", "Question Type", "Question",
    "Option1", "option2", "option3", "Option4", "Option5", "Option6",
    "Marks", "Negative Marks", "Hint", "Explanation",
    "Correct Answer", "True & False", "Fill in the blanks",
]


# ============================================================
# HELPERS
# ============================================================

def _workbook_bytes(headers: list[str], rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def native_file(rows: list[list]) -> bytes:
    return _workbook_bytes(NATIVE_HEADERS, rows)


def edu_row(**kw) -> list:
    """Build one Edu Expression row by header name; unspecified cells are blank."""
    defaults = {
        "Difficulty Level": "E", "Question Type": "M", "Question": "Q?",
        "Marks": 4, "Negative Marks": 1,
    }
    values = {**defaults, **kw}
    return [values.get(h, "") for h in EDU_HEADERS]


def edu_file(rows: list[list]) -> bytes:
    return _workbook_bytes(EDU_HEADERS, rows)


def upload(client, headers, subject_id, filename, content, content_type=XLSX):
    return client.post(
        f"/api/v1/subjects/{subject_id}/items/bulk",
        headers=headers,
        files={"file": (filename, content, content_type)},
    )


def find_item(client, headers, subject_id, search):
    resp = client.get(
        f"/api/v1/subjects/{subject_id}/items",
        params={"search": search},
        headers=headers,
    )
    assert resp.status_code == 200, resp.json()
    items = resp.json()["items"]
    assert len(items) == 1, f"expected exactly one item matching '{search}', got {len(items)}"
    return items[0]


@pytest.fixture
def subject(client, admin_headers):
    s = create_subject(client, admin_headers, "Imports")
    assign_teacher(client, admin_headers, s["id"], TEACHER_ID)
    return s


# ============================================================
# EDU EXPRESSION LAYOUT
# ============================================================

def test_edu_layout_imports_all_question_types(client, teacher_headers, subject):
    content = edu_file([
        edu_row(**{"Question Type": "M", "Question": "MCQ alpha", "Option1": "w", "option2": "x",
                   "option3": "y", "Option4": "z", "Correct Answer": 2}),
        edu_row(**{"Question Type": "T", "Question": "TF alpha", "True & False": "True"}),
        edu_row(**{"Question Type": "F", "Question": "Blank alpha", "Fill in the blanks": "blue"}),
        edu_row(**{"Question Type": "S", "Question": "Essay alpha"}),
    ])

    resp = upload(client, teacher_headers, subject["id"], "export.xlsx", content)

    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["total_rows"] == 4
    assert data["successful_rows"] == 4, data["errors"]
    assert data["failed_rows"] == 0


def test_edu_mcq_answer_index_maps_to_letter_and_difficulty(client, teacher_headers, subject):
    content = edu_file([
        edu_row(**{"Difficulty Level": "H", "Question": "Index mapping", "Option1": "a",
                   "option2": "b", "option3": "c", "Option4": "d", "Correct Answer": 3}),
    ])
    assert upload(client, teacher_headers, subject["id"], "export.xlsx", content).json()["successful_rows"] == 1

    item = find_item(client, teacher_headers, subject["id"], "Index mapping")
    assert item["item_type"] == "mcq_single"
    assert item["correct_answers"] == ["C"]          # 3rd option -> C
    assert item["difficulty"] == "hard"              # H -> hard
    assert item["marks"] == 4.0
    assert item["negative_marks"] == 1.0
    assert [o["key"] for o in item["options"]] == ["A", "B", "C", "D"]


def test_edu_multiple_correct_indexes_become_mcq_multi(client, teacher_headers, subject):
    content = edu_file([
        edu_row(**{"Question": "Multi answer", "Option1": "a", "option2": "b",
                   "option3": "c", "Option4": "d", "Correct Answer": "1,3"}),
    ])
    assert upload(client, teacher_headers, subject["id"], "export.xlsx", content).json()["successful_rows"] == 1

    item = find_item(client, teacher_headers, subject["id"], "Multi answer")
    assert item["item_type"] == "mcq_multi"
    assert item["correct_answers"] == ["A", "C"]


def test_edu_sixth_option_row_is_rejected_others_still_import(client, teacher_headers, subject):
    content = edu_file([
        edu_row(**{"Question": "Too many options", "Option1": "a", "option2": "b", "option3": "c",
                   "Option4": "d", "Option5": "e", "Option6": "f", "Correct Answer": 1}),
        edu_row(**{"Question": "Fine question", "Option1": "a", "option2": "b",
                   "option3": "c", "Option4": "d", "Correct Answer": 1}),
    ])

    resp = upload(client, teacher_headers, subject["id"], "export.xlsx", content)

    data = resp.json()
    assert resp.status_code == 200
    assert data["successful_rows"] == 1
    assert data["failed_rows"] == 1
    assert data["errors"][0]["row"] == 2
    assert "6th option" in data["errors"][0]["error"]


def test_edu_blank_sixth_option_is_fine(client, teacher_headers, subject):
    """Option6 present as a column but empty must NOT trigger the rejection."""
    content = edu_file([
        edu_row(**{"Question": "Five options", "Option1": "a", "option2": "b", "option3": "c",
                   "Option4": "d", "Option5": "e", "Option6": "", "Correct Answer": 5}),
    ])
    data = upload(client, teacher_headers, subject["id"], "export.xlsx", content).json()
    assert data["successful_rows"] == 1, data["errors"]


def test_edu_hint_is_prepended_to_explanation(client, teacher_headers, subject):
    content = edu_file([
        edu_row(**{"Question": "Hint merge", "Option1": "a", "option2": "b", "option3": "c",
                   "Option4": "d", "Correct Answer": 1, "Hint": "Think small",
                   "Explanation": "Because reasons"}),
        edu_row(**{"Question": "Hint only", "Option1": "a", "option2": "b", "option3": "c",
                   "Option4": "d", "Correct Answer": 1, "Hint": "Just a hint"}),
    ])
    assert upload(client, teacher_headers, subject["id"], "export.xlsx", content).json()["successful_rows"] == 2

    both = find_item(client, teacher_headers, subject["id"], "Hint merge")
    assert both["explanation"] == "Hint: Think small\n\nBecause reasons"

    only_hint = find_item(client, teacher_headers, subject["id"], "Hint only")
    assert only_hint["explanation"] == "Hint: Just a hint"


def test_edu_fill_blank_imports_as_short_answer(client, teacher_headers, subject):
    """Fill-in-the-blank is kept for manual review, i.e. stored as short_answer."""
    content = edu_file([
        edu_row(**{"Question Type": "F", "Question": "Sky colour blank", "Fill in the blanks": "blue"}),
    ])
    data = upload(client, teacher_headers, subject["id"], "export.xlsx", content).json()
    # If this fails, check how normalize_correct_answers handles options=None
    # with free text (it already handles numeric answers this way).
    assert data["successful_rows"] == 1, data["errors"]

    item = find_item(client, teacher_headers, subject["id"], "Sky colour blank")
    assert item["item_type"] == "short_answer"


def test_edu_true_false_matches_native_true_false(client, teacher_headers, subject):
    """
    Regression for the boolean-cell bug: an Edu True/False row must be stored
    exactly like the same row uploaded through the native template.
    """
    native = native_file([
        ["Native TF true", "true_false", "True", "False", "", "", "True", "", 1, 0, "", "easy"],
        ["Native TF false", "true_false", "True", "False", "", "", "False", "", 1, 0, "", "easy"],
    ])
    assert upload(client, teacher_headers, subject["id"], "native.xlsx", native).json()["successful_rows"] == 2

    edu = edu_file([
        edu_row(**{"Question Type": "T", "Question": "Edu TF true", "True & False": True}),    # real boolean cell
        edu_row(**{"Question Type": "T", "Question": "Edu TF false", "True & False": "False"}),  # text
    ])
    assert upload(client, teacher_headers, subject["id"], "edu.xlsx", edu).json()["successful_rows"] == 2

    n_true = find_item(client, teacher_headers, subject["id"], "Native TF true")
    n_false = find_item(client, teacher_headers, subject["id"], "Native TF false")
    e_true = find_item(client, teacher_headers, subject["id"], "Edu TF true")
    e_false = find_item(client, teacher_headers, subject["id"], "Edu TF false")

    assert e_true["correct_answers"] == n_true["correct_answers"]
    assert e_false["correct_answers"] == n_false["correct_answers"]
    assert e_true["correct_answers"] != e_false["correct_answers"]


def test_edu_unrecognized_question_type_fails_only_that_row(client, teacher_headers, subject):
    content = edu_file([
        edu_row(**{"Question Type": "Z", "Question": "Weird type"}),
        edu_row(**{"Question Type": "S", "Question": "Good essay"}),
    ])
    data = upload(client, teacher_headers, subject["id"], "export.xlsx", content).json()
    assert data["successful_rows"] == 1
    assert data["failed_rows"] == 1
    assert "Question Type" in data["errors"][0]["error"]


def test_edu_unreadable_true_false_value_is_rejected_not_defaulted(client, teacher_headers, subject):
    content = edu_file([
        edu_row(**{"Question Type": "T", "Question": "Blank TF", "True & False": ""}),
    ])
    data = upload(client, teacher_headers, subject["id"], "export.xlsx", content).json()
    assert data["successful_rows"] == 0
    assert data["failed_rows"] == 1


# ============================================================
# REAL SAMPLE FILE (legacy .xls) - optional smoke test
# ============================================================

SAMPLE_XLS = Path(__file__).parent / "fixtures" / "SampleQuestion.xls"


@pytest.mark.skipif(not SAMPLE_XLS.exists(), reason="fixtures/SampleQuestion.xls not present")
def test_real_sample_xls_imports_fully(client, teacher_headers, subject):
    resp = upload(client, teacher_headers, subject["id"], "SampleQuestion.xls",
                  SAMPLE_XLS.read_bytes(), XLS)

    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["total_rows"] == 9
    assert data["successful_rows"] == 9, data["errors"]
    assert data["failed_rows"] == 0

    # The sample's single True/False row is stored as "true" - guards against
    # xlrd handing back the Excel boolean as 1.
    listing = client.get(f"/api/v1/subjects/{subject['id']}/items", headers=teacher_headers).json()
    tf_items = [i for i in listing["items"] if i["item_type"] == "true_false"]
    assert len(tf_items) == 1

    native = native_file([["Reference TF", "true_false", "True", "False", "", "", "True", "", 1, 0, "", "easy"]])
    upload(client, teacher_headers, subject["id"], "ref.xlsx", native)
    reference = find_item(client, teacher_headers, subject["id"], "Reference TF")
    assert tf_items[0]["correct_answers"] == reference["correct_answers"]


# ============================================================
# FORMAT DETECTION (content, not filename)
# ============================================================

def test_native_workbook_saved_with_xls_name_still_imports(client, teacher_headers, subject):
    """Reader is chosen from file bytes, so a mislabeled extension doesn't matter."""
    content = native_file([
        ["Mislabeled file?", "mcq_single", "A", "B", "C", "D", "A", "", 1, 0, "", "easy"],
    ])
    resp = upload(client, teacher_headers, subject["id"], "questions.xls", content, XLS)
    assert resp.status_code == 200, resp.json()
    assert resp.json()["successful_rows"] == 1


def test_unrecognized_layout_is_rejected(client, teacher_headers, subject):
    content = _workbook_bytes(["foo", "bar"], [["1", "2"]])
    resp = upload(client, teacher_headers, subject["id"], "random.xlsx", content)
    assert resp.status_code == 400
    assert "unrecognized" in resp.json()["detail"].lower()


def test_native_file_missing_item_type_column_is_rejected(client, teacher_headers, subject):
    """Behaviour change: this used to say 'Missing columns', now 'Unrecognized file layout'."""
    content = _workbook_bytes(["question_text", "option_a"], [["Q?", "A"]])
    resp = upload(client, teacher_headers, subject["id"], "partial.xlsx", content)
    assert resp.status_code == 400


def test_native_headers_are_case_insensitive(client, teacher_headers, subject):
    headers = [h.upper() for h in NATIVE_HEADERS]
    content = _workbook_bytes(headers, [
        ["Upper headers", "mcq_single", "A", "B", "C", "D", "A", "", 1, 0, "", "easy"],
    ])
    resp = upload(client, teacher_headers, subject["id"], "upper.xlsx", content)
    assert resp.status_code == 200, resp.json()
    assert resp.json()["successful_rows"] == 1


# ============================================================
# FILE-LEVEL FAILURES
# ============================================================

def test_corrupt_file_is_rejected(client, teacher_headers, subject):
    resp = upload(client, teacher_headers, subject["id"], "broken.xlsx", b"this is not an excel file")
    assert resp.status_code == 400
    assert "unreadable" in resp.json()["detail"].lower()


def test_header_only_file_is_rejected_as_empty(client, teacher_headers, subject):
    content = _workbook_bytes(NATIVE_HEADERS, [])
    resp = upload(client, teacher_headers, subject["id"], "empty.xlsx", content)
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


def test_file_over_5mb_is_rejected(client, teacher_headers, subject):
    oversized = b"0" * (5 * 1024 * 1024 + 1)
    resp = upload(client, teacher_headers, subject["id"], "huge.xlsx", oversized)
    assert resp.status_code == 413
    assert "5MB" in resp.json()["detail"]


def test_file_at_exactly_5mb_passes_the_size_check(client, teacher_headers, subject):
    """Boundary: exactly 5MB is allowed through the size gate (then fails as unreadable)."""
    at_limit = b"0" * (5 * 1024 * 1024)
    resp = upload(client, teacher_headers, subject["id"], "limit.xlsx", at_limit)
    assert resp.status_code == 400          # not 413
    assert "unreadable" in resp.json()["detail"].lower()


def test_unassigned_teacher_cannot_bulk_upload(client, admin_headers, teacher_headers):
    """Upload must respect subject assignment like every other item endpoint."""
    unassigned = create_subject(client, admin_headers, "Unassigned")
    content = native_file([
        ["Nope", "mcq_single", "A", "B", "C", "D", "A", "", 1, 0, "", "easy"],
    ])
    resp = upload(client, teacher_headers, unassigned["id"], "q.xlsx", content)
    assert resp.status_code == 403