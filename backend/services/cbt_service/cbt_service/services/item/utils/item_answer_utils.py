from fastapi import HTTPException




def build_true_false_options() -> list[dict]:
    """
    true_false items always have exactly these two options, regardless of
    what text was typed into option_a/option_b — 'true'/'false' IS the key,
    no letter-key indirection needed like MCQ requires.
    """
    return [
        {"key": "true", "text": "True"},
        {"key": "false", "text": "False"},
    ]


def normalize_boolean_answer(raw_answers: list[str] | None) -> list[str] | None:
    """
    Resolves any reasonable true/false spelling (True, TRUE, true, A, yes, 1)
    to the canonical lowercase 'true'/'false' — matching what the frontend
    already submits for this item type.
    """
    if not raw_answers:
        return raw_answers

    TRUE_VALUES = {"true", "a", "yes", "1"}
    FALSE_VALUES = {"false", "b", "no", "0"}

    resolved = []
    for ans in raw_answers:
        norm = ans.strip().lower()
        if norm in TRUE_VALUES:
            resolved.append("true")
        elif norm in FALSE_VALUES:
            resolved.append("false")
        else:
            resolved.append(ans)  # left as-is, caught by validation
    return resolved


def normalize_correct_answers(
    options: list[dict] | None,
    correct_answers: list[str] | None,
) -> list[str] | None:
    """
    Resolves correct_answers to canonical option keys, accepting either the
    key ('A') or the option text ('True') in any case — since both bulk
    Excel upload and manual item creation have historically sent either
    form inconsistently. Returns correct_answers unchanged for item types
    with no options (numeric, short_answer).
    """
    if not correct_answers or not options:
        return correct_answers

    key_by_key = {opt["key"].strip().lower(): opt["key"] for opt in options}
    key_by_text = {opt["text"].strip().lower(): opt["key"] for opt in options}

    resolved = []
    for ans in correct_answers:
        ans_norm = ans.strip().lower()
        resolved.append(key_by_key.get(ans_norm) or key_by_text.get(ans_norm) or ans)
    return resolved


def assert_correct_answers_valid(
    options: list[dict] | None,
    correct_answers: list[str] | None,
) -> None:
    """Raises 400 if any correct_answers entry doesn't resolve to a real option key."""
    if not options or not correct_answers:
        return
    valid_keys = {opt["key"] for opt in options}
    bad = [a for a in correct_answers if a not in valid_keys]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=(
                f"correct_answers {bad} doesn't match any option key or text "
                f"(valid keys: {', '.join(sorted(valid_keys))})"
            ),
        )