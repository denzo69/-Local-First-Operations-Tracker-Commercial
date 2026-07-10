from decimal import Decimal, ROUND_HALF_UP


MONEY_QUANT = Decimal("0.01")


def parse_decimal(value: str | int | float | Decimal | None, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    return Decimal(str(value).replace(",", "."))


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def vat_included_breakdown(gross_total: Decimal, vat_percent: Decimal) -> tuple[Decimal, Decimal]:
    if gross_total == 0 or vat_percent == 0:
        return money(gross_total), Decimal("0.00")
    net = gross_total / (Decimal("1") + (vat_percent / Decimal("100")))
    vat = gross_total - net
    return money(net), money(vat)


def line_total(quantity: Decimal, unit_price_including_vat: Decimal) -> Decimal:
    return money(quantity * unit_price_including_vat)


def sum_money(values) -> Decimal:
    return money(sum((parse_decimal(value) for value in values), Decimal("0")))
