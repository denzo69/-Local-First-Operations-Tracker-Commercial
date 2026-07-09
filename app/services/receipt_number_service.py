def format_receipt_number(year: int, sequence: int, prefix: str = "", padding: int = 6) -> str:
    """Format a receipt number using year and a padded sequence.

    Example: 2026-000001 or PESULA-2026-000001.
    """
    base = f"{year}-{sequence:0{padding}d}"
    return f"{prefix}{base}" if prefix else base
