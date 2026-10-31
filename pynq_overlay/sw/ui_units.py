"""Exact local conversions; hardware protocols always receive integer base units."""
from decimal import Decimal, InvalidOperation, localcontext
import re

FREQUENCY_UNITS = ('Hz', 'kHz', 'MHz', 'GHz')
SYMBOL_UNITS = ('Sym/s', 'kSym/s', 'MSym/s')
SCALES = dict(zip(FREQUENCY_UNITS, (1, 1000, 1000000, 1000000000)))
SCALES.update(zip(SYMBOL_UNITS, (1, 1000, 1000000)))
_NUMBER = re.compile(r'\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z')


def parse_frequency(text, unit='Hz', minimum=0, maximum=2100000000):
    """Reject fractional base units and overflow instead of rounding a draft."""
    text = str(text).strip()
    if unit not in SCALES or len(text) > 64 or not _NUMBER.fullmatch(text):
        raise ValueError('请输入非负十进制数')
    try:
        with localcontext() as ctx:
            ctx.prec = 80
            value = Decimal(text) * SCALES[unit]
            if value != value.to_integral_value():
                raise ValueError('结果必须是整数 Hz / Sym/s')
            if not minimum <= value <= maximum:
                raise ValueError('数值超出范围')
            return int(value)
    except InvalidOperation as exc:
        raise ValueError('无效数值') from exc


def format_frequency(value, unit='Hz'):
    """Plain decimal text retaining every integer Hz, even after repeated switches."""
    if unit not in SCALES or type(value) is not int or value < 0:
        raise ValueError('无效单位或数值')
    with localcontext() as ctx:
        ctx.prec = max(32, len(str(value)) + 12)
        text = format(Decimal(value) / SCALES[unit], 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text
