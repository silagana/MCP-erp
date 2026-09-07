from datetime import date
from decimal import Decimal


def fmt_moneda(value) -> str:
    if value is None:
        return "$ -"
    d = Decimal(str(value))
    parts = f"{abs(d):.2f}".split(".")
    integer_part, decimal_part = parts[0], parts[1]
    grouped: list[str] = []
    for i, digit in enumerate(reversed(integer_part)):
        if i > 0 and i % 3 == 0:
            grouped.append(".")
        grouped.append(digit)
    sign = "-" if d < 0 else ""
    return f"$ {sign}{''.join(reversed(grouped))},{decimal_part}"


def fmt_fecha(d: date | None) -> str:
    if d is None:
        return ""
    return d.strftime("%d/%m/%Y")


def parse_importe_ar(s: str) -> Decimal:
    """'140.000,00' → Decimal('140000.00')"""
    return Decimal(s.strip().replace(".", "").replace(",", "."))
