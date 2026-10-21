"""Conservative derived values. Raw evidence is never overwritten."""
import re


def salary(raw, fields=()):
    raw = raw or ''
    # A payout schedule is not the period of the advertised amount.
    first = raw.splitlines()[0] if raw else ''
    num = r'(\d[\d \u00a0\u202f]*)'
    result = {'source_raw': raw, 'lower': None, 'upper': None, 'currency': None,
              'amount_period': None, 'tax_basis': None, 'payout_frequency_raw': [],
              'bonuses': [], 'advance': None, 'parser': 'explicit_salary_line_v1'}
    if '₽' in first or re.search(r'руб', first, re.I):
        result['currency'] = 'RUB'
    amount = lambda value: int(re.sub(r'\D', '', value))
    match = re.match(r'^\s*(от|до)?\s*' + num + r'\s*(?:[–—−-]\s*' + num + r')?\s*(?:₽|руб)', first, re.I)
    if match:
        direction, low, high = match.groups()
        if high:
            result.update(lower=amount(low), upper=amount(high), bounds_kind='range')
        elif direction and direction.lower() == 'до':
            result.update(upper=amount(low), bounds_kind='up_to')
        elif direction:
            result.update(lower=amount(low), bounds_kind='from')
        else:
            result.update(lower=amount(low), upper=amount(low), bounds_kind='exact')
    for pattern, value in [(r'за\s+смену', 'shift'), (r'за\s+час|в\s+час', 'hour'),
                           (r'в\s+месяц|за\s+месяц', 'month'), (r'за\s+вахту', 'rotation'),
                           (r'за\s+день|в\s+день', 'day')]:
        if re.search(pattern, first, re.I):
            result['amount_period'] = value
    tax = []
    for pattern, value in [(r'на руки', 'net'), (r'до (?:вычета )?налогов|до вычета НДФЛ', 'gross')]:
        if re.search(pattern, raw, re.I):
            tax.append(value)
    result['tax_basis'] = tax[0] if len(tax) == 1 else None
    result['tax_evidence'] = tax
    result['payout_frequency_raw'] = [f['value'] for f in fields if f.get('name') == 'Частота выплат']
    # Bonuses, advances and negations remain in raw text; no inferred arithmetic.
    result['unparsed_evidence_retained'] = True
    return result
