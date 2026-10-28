"""Versioned title-only keyword signals; never filter or infer from descriptions."""
import re
import unicodedata

VERSION = 'military_title_v1'
COLUMNS = ('military', 'military_keywords', 'military_rule_version')
# Exact tokens and explicit word stems. 'контракт' is deliberately absent.
RULES = (
    ('БПЛА', 'exact', 'бпла'),
    ('ЛНР', 'exact', 'лнр'),
    ('ДНР', 'exact', 'днр'),
    ('СВО', 'exact', 'сво'),
    ('Донецк', 'prefix', 'донецк'),
    ('военный', 'prefix', 'военн'),
    ('тыл', 'exact', 'тыл'),
    ('тыловой', 'prefix', 'тылов'),
    ('ВАИ', 'exact', 'ваи'),
)


def military_flags(title):
    if not isinstance(title, str) or not title.strip():
        raise ValueError('Military title flag requires a nonempty observed title')
    normalized = unicodedata.normalize('NFKC', title).casefold().replace('ё', 'е')
    tokens = re.findall(r'[^\W_]+', normalized)
    matched = [label for label, mode, term in RULES
               if any(token == term if mode == 'exact' else token.startswith(term)
                      for token in tokens)]
    return dict(military=str(bool(matched)), military_keywords='; '.join(matched),
                military_rule_version=VERSION)
