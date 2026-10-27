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


@contextmanager
def operation_locked(diag):
    """Only one actual CLI action may write/navigate for this run at a time."""
    diag.mkdir(parents=True, exist_ok=True)
    with (diag / 'operation.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HumanRequired('Another collector action is running for this run; do not start a duplicate') from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def load(diag):
    path = diag / 'failures.json'
    ledger = json.loads(path.read_text()) if path.exists() else {
        'events': [], 'stages': {}, 'failures': 0, 'state': 'READY', 'retry_started': False}
    # Old stopped ledgers stay stopped. The new policy never grants human consent.
    ledger.setdefault('retry_limit', 5)
    ledger.setdefault('retries_started', sum(e.get('event') == 'retry_started' for e in ledger['events']))
    ledger.setdefault('budget_start_failures', 0)
    if ledger['state'] == 'RETRY_STARTED':
        ledger.setdefault('retry_action_started', True)  # Legacy format cannot prove the action never began.
    if ledger['state'] == 'RETRY_STARTED' and not ledger.get('active_retry_stage'):
        ledger['active_retry_stage'] = next((e['stage'] for e in reversed(ledger['events'])
            if e.get('event') == 'retry_started'), None)
    return ledger


def save(diag, ledger):
    path = diag / 'failures.json'
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def ensure_allowed(diag, stage, retry_reason=None, *, action=False):
    """Reserve one retry per failed action; duplicate checks reuse that reservation."""
    with locked(diag):
        ledger = load(diag)
        if ledger['state'] == 'NEEDS_HUMAN':
            if stage in ledger.get('offline_authorization', {}).get('stages', []):
                return
            raise HumanRequired('NEEDS_HUMAN: collection stopped; fresh human instruction required')
        if ledger['state'] == 'RETRY_STARTED':
            if ledger.get('active_retry_stage') != stage:
                raise HumanRequired('Finish the active retry with stage-ok or record-error before another stage')
            if action:
                if ledger.get('retry_action_started'):
                    raise HumanRequired('Retry action already started; confirm interruption and record-error before restarting it')
                ledger['retry_action_started'] = True
                save(diag, ledger)
            return  # Duplicate checks are harmless; an action can claim the retry only once.
        if ledger['failures'] > ledger['budget_start_failures'] and ledger['state'] != 'RECOVERED':
            if not retry_reason or not retry_reason.strip():
                raise HumanRequired('Retry requires --retry-reason describing the observed correction')
            if ledger['retries_started'] >= ledger['retry_limit']:
                raise HumanRequired('NEEDS_HUMAN: retry budget exhausted; fresh human instruction required')
            ledger['retries_started'] += 1
            ledger['retry_started'] = True
            ledger['retry_action_started'] = action
            ledger['active_retry_stage'] = stage
            ledger['state'] = 'RETRY_STARTED'
            ledger['events'].append(dict(at=datetime.now(timezone.utc).isoformat(), stage=stage,
                event='retry_started', reason=retry_reason, retry_number=ledger['retries_started']))
            save(diag, ledger)


def succeeded(diag, stage, evidence):
    with locked(diag):
        ledger = load(diag)
        if ledger['state'] == 'RETRY_STARTED':
            if ledger.get('active_retry_stage') != stage:
                raise ValueError('Success must confirm the active retry stage')
            ledger['state'] = 'RECOVERED'
            ledger['retry_started'] = False
            ledger.pop('retry_action_started', None)
            ledger.pop('active_retry_stage', None)
            ledger['events'].append(dict(at=datetime.now(timezone.utc).isoformat(), stage=stage,
                                         event='recovered', evidence=evidence))
            save(diag, ledger)  # Success does not restore the shared retry budget.


def record(diag, stage, message, action, url=None, agent=None, immediate=False):
    with locked(diag):
        ledger = load(diag)
        if ledger['state'] == 'RETRY_STARTED' and ledger.get('active_retry_stage') != stage and not immediate:
            raise ValueError('Record the actual active retry stage; another stage cannot cancel it')
        stopped = ledger['state'] == 'NEEDS_HUMAN'
        ledger.pop('offline_authorization', None)
        ledger['retry_started'] = False
        ledger.pop('retry_action_started', None)
        ledger.pop('active_retry_stage', None)
        current = ledger['stages'].setdefault(stage, {'failures': 0, 'state': 'RETRY_AVAILABLE'})
        current['failures'] += 1
        ledger['failures'] += 1
        ledger['state'] = 'NEEDS_HUMAN' if stopped or immediate or ledger['retries_started'] >= ledger['retry_limit'] else 'RETRY_AVAILABLE'
        current['state'] = ledger['state']
        event = dict(at=datetime.now(timezone.utc).isoformat(), stage=stage, event='failure',
            attempt=ledger['failures'], state=ledger['state'], message=message,
            action=action, url=url, agent=agent, retry_budget_remaining=0 if ledger['state'] == 'NEEDS_HUMAN' else ledger['retry_limit'] - ledger['retries_started'])
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


def authorize_offline(diag, instruction):
    """Caller must have a fresh human instruction; this never permits browser work."""
    if not instruction.strip():
        raise ValueError('Human instruction is required')
    with locked(diag):
        ledger = load(diag)
        authorization = dict(at=datetime.now(timezone.utc).isoformat(), instruction=instruction,
                             stages=['finalize', 'publish'])
        ledger['offline_authorization'] = authorization
        ledger['events'].append(dict(event='human_authorization', **authorization))
        save(diag, ledger)


def authorize_browser(diag, instruction, authorization_id):
    """Explicit human consent starts a bounded new budget, never erases history."""
    if not instruction.strip() or not authorization_id.strip():
        raise ValueError('Fresh human instruction and a stable authorization-id are required')
    with locked(diag):
        ledger = load(diag)
        if any(e.get('event') == 'human_browser_authorization' and e.get('authorization_id') == authorization_id
               for e in ledger['events']):
            if ledger['state'] == 'NEEDS_HUMAN':
                raise HumanRequired('This human authorization was already used; a fresh instruction is required')
            return  # Replayed CLI invocation cannot replenish the budget.
        if ledger['state'] != 'NEEDS_HUMAN':
            raise ValueError('Browser resume requires a stopped run')
        ledger.pop('offline_authorization', None)
        ledger.pop('active_retry_stage', None)
        ledger.pop('retry_action_started', None)
        ledger['budget_start_failures'] = ledger['failures']
        ledger['retries_started'] = 0
        ledger['retry_limit'] = 5
        ledger['retry_started'] = False
        ledger['state'] = 'READY'
        ledger['events'].append(dict(at=datetime.now(timezone.utc).isoformat(),
            event='human_browser_authorization', instruction=instruction,
            authorization_id=authorization_id, budget_start_failures=ledger['failures'], retry_limit=5))
        save(diag, ledger)
        (diag/'failure-report.md').write_text(
            'RESUMED_BY_HUMAN\n\nИстория ошибок сохранена. Разрешена новая ограниченная попытка '
            'с пятью повторами; ограничения сайта требуют немедленной остановки.\n')
