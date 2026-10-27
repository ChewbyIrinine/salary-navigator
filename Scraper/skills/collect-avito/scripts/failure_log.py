"""Persistent per-run failure budget shared by all agents; no automatic retries."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json


class HumanRequired(RuntimeError):
    pass


@contextmanager
def locked(diag):
    diag.mkdir(parents=True, exist_ok=True)
    with (diag / 'failure.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def load(diag):
    path = diag / 'failures.json'
    return json.loads(path.read_text()) if path.exists() else {
        'events': [], 'stages': {}, 'failures': 0, 'state': 'READY', 'retry_started': False}


def save(diag, ledger):
    path = diag / 'failures.json'
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def ensure_allowed(diag, stage, retry_reason=None):
    """A retry needs an explicit reason; human stop applies across stages/agents."""
    with locked(diag):
        ledger = load(diag)
        if ledger['state'] == 'NEEDS_HUMAN':
            raise HumanRequired('NEEDS_HUMAN: collection stopped; human instruction required, no new navigation')
        if ledger['failures'] and ledger['state'] != 'RECOVERED':
            if not retry_reason:
                raise HumanRequired('One retry is available only with --retry-reason explaining the observed correction')
            if ledger['retry_started']:
                ledger['state'] = 'NEEDS_HUMAN'
                save(diag, ledger)
                (diag/'failure-report.md').write_text(
                    'NEEDS_HUMAN\n\nЕдинственный повтор уже начат, его успешное завершение '
                    'не подтверждено. Новые попытки запрещены до инструкции человека.\n')
                raise HumanRequired('NEEDS_HUMAN: the single retry was already started; do not repeat it')
            ledger['retry_started'] = True
            ledger['state'] = 'RETRY_STARTED'
            ledger['events'].append(dict(at=datetime.now(timezone.utc).isoformat(), stage=stage,
                                         event='retry_started', reason=retry_reason))
            save(diag, ledger)


def succeeded(diag, stage, evidence):
    with locked(diag):
        ledger = load(diag)
        if ledger['state'] == 'RETRY_STARTED':
            ledger['state'] = 'RECOVERED'
            ledger['events'].append(dict(at=datetime.now(timezone.utc).isoformat(), stage=stage,
                                         event='recovered', evidence=evidence))
            save(diag, ledger)  # failures stay counted; another error stops the run


def record(diag, stage, message, action, url=None, agent=None, immediate=False):
    with locked(diag):
        ledger = load(diag)
        current = ledger['stages'].setdefault(stage, {'failures': 0, 'state': 'RETRY_AVAILABLE'})
        current['failures'] += 1
        ledger['failures'] += 1
        ledger['state'] = 'NEEDS_HUMAN' if immediate or ledger['failures'] >= 2 else 'RETRY_AVAILABLE'
        current['state'] = ledger['state']
        event = dict(at=datetime.now(timezone.utc).isoformat(), stage=stage, event='failure',
            attempt=ledger['failures'], state=ledger['state'], message=message,
            action=action, url=url, agent=agent, retry_budget_remaining=int(current['state'] != 'NEEDS_HUMAN'))
        ledger['events'].append(event)
        save(diag, ledger)
        # Compact report contains no DOM dumps, tokens or local session contents.
        (diag/'failure-report.md').write_text(
            f'{event["state"]}\n\nЭтап: {stage}\nДействие: {action}\n'
            f'Страница: {url or "неизвестна"}\nОшибка: {message}\n'
            f'Неудачных попыток: {ledger["failures"]}; '
            f'осталось повторов: {event["retry_budget_remaining"]}.\n'
            'Смена агента, вкладки или URL не сбрасывает счётчик. '
            'При NEEDS_HUMAN новые переходы запрещены до инструкции человека.\n')
        return event
