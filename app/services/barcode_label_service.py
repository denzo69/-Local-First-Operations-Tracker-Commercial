from xml.sax.saxutils import escape


_EAN_L = {
    "0": "0001101", "1": "0011001", "2": "0010011", "3": "0111101", "4": "0100011",
    "5": "0110001", "6": "0101111", "7": "0111011", "8": "0110111", "9": "0001011",
}
_EAN_G = {
    "0": "0100111", "1": "0110011", "2": "0011011", "3": "0100001", "4": "0011101",
    "5": "0111001", "6": "0000101", "7": "0010001", "8": "0001001", "9": "0010111",
}
_EAN_R = {digit: pattern.translate(str.maketrans("01", "10")) for digit, pattern in _EAN_L.items()}
_EAN13_PARITY = {
    "0": "LLLLLL", "1": "LLGLGG", "2": "LLGGLG", "3": "LLGGGL", "4": "LGLLGG",
    "5": "LGGLLG", "6": "LGGGLL", "7": "LGLGLG", "8": "LGLGGL", "9": "LGGLGL",
}
_CODE39 = {
    "0": "nnnwwnwnn", "1": "wnnwnnnnw", "2": "nnwwnnnnw", "3": "wnwwnnnnn", "4": "nnnwwnnnw",
    "5": "wnnwwnnnn", "6": "nnwwwnnnn", "7": "nnnwnnwnw", "8": "wnnwnnwnn", "9": "nnwwnnwnn",
    "A": "wnnnnwnnw", "B": "nnwnnwnnw", "C": "wnwnnwnnn", "D": "nnnnwwnnw", "E": "wnnnwwnnn",
    "F": "nnwnwwnnn", "G": "nnnnnwwnw", "H": "wnnnnwwnn", "I": "nnwnnwwnn", "J": "nnnnwwwnn",
    "K": "wnnnnnnww", "L": "nnwnnnnww", "M": "wnwnnnnwn", "N": "nnnnwnnww", "O": "wnnnwnnwn",
    "P": "nnwnwnnwn", "Q": "nnnnnnwww", "R": "wnnnnnwwn", "S": "nnwnnnwwn", "T": "nnnnwnwwn",
    "U": "wwnnnnnnw", "V": "nwwnnnnnw", "W": "wwwnnnnnn", "X": "nwnnwnnnw", "Y": "wwnnwnnnn",
    "Z": "nwwnwnnnn", "-": "nwnnnnwnw", ".": "wwnnnnwnn", " ": "nwwnnnwnn", "$": "nwnwnwnnn",
    "/": "nwnwnnnwn", "+": "nwnnnwnwn", "%": "nnnwnwnwn",
    "*": "nwnnwnwnn",
}


def _svg_from_bits(bits: str, text: str) -> str:
    quiet = "0000000000"
    bits = quiet + bits + quiet
    width = len(bits)
    bars = []
    for x, bit in enumerate(bits):
        if bit == "1":
            bars.append(f'<rect x="{x}" y="0" width="1" height="48"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Barcode {escape(text)}" '
        f'viewBox="0 0 {width} 58" preserveAspectRatio="none"><g fill="#111">{"".join(bars)}</g>'
        f'<text x="{width / 2}" y="57" text-anchor="middle" font-family="monospace" font-size="7">{escape(text)}</text></svg>'
    )


def _ean_bits(code: str) -> str:
    if len(code) == 13:
        parity = _EAN13_PARITY[code[0]]
        left = "".join((_EAN_L if mode == "L" else _EAN_G)[digit] for mode, digit in zip(parity, code[1:7]))
        right = "".join(_EAN_R[digit] for digit in code[7:])
        return "101" + left + "01010" + right + "101"
    if len(code) == 12:
        return _ean_bits("0" + code)
    if len(code) == 8:
        left = "".join(_EAN_L[digit] for digit in code[:4])
        right = "".join(_EAN_R[digit] for digit in code[4:])
        return "101" + left + "01010" + right + "101"
    raise ValueError("Only EAN-8, UPC-A, and EAN-13 can use retail EAN bars.")


def barcode_svg(code: str, symbology: str | None = None) -> str:
    value = (code or "").strip().upper()
    if not value:
        raise ValueError("A barcode value is required.")
    kind = (symbology or "").lower()
    if kind in {"ean8", "ean13", "upca"} and value.isdigit():
        return _svg_from_bits(_ean_bits(value), value)
    if not all(character in _CODE39 for character in value):
        raise ValueError("Barcode labels support EAN-8, UPC-A, EAN-13, or Code 39 compatible SKU values.")
    bits = []
    for character in f"*{value}*":
        pattern = _CODE39[character]
        for index, size in enumerate(pattern):
            bits.append(("1" if index % 2 == 0 else "0") * (3 if size == "w" else 1))
        bits.append("0")
    return _svg_from_bits("".join(bits), value)
