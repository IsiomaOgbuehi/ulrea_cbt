def escape_like(term: str) -> str:
    """Treat %, _ and \\ in user input literally inside an ILIKE pattern."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")