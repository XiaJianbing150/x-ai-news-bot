"""Per-run source diagnostics. Never retain response bodies, headers or secrets."""
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
from urllib.parse import urlsplit, urlunsplit

_results = []
_attempts = ContextVar('source_attempts', default=None)


def reset_health():
    _results.clear()


def get_health():
    return deepcopy(_results)


def record_attempt(url, http_status=None, error=None):
    attempts = _attempts.get()
    if attempts is None:
        return
    parsed = urlsplit(url)
    endpoint = urlunsplit((parsed.scheme, parsed.hostname or '', parsed.path, '', ''))
    attempts.append({'endpoint': endpoint, 'http_status': http_status,
                     'status': 'failed' if error or http_status != 200 else 'ok',
                     'error': error})


def invalid_response(reason):
    attempts = _attempts.get()
    if attempts:
        attempts[-1].update(status='failed', error=reason)


def record_skip(source, reason):
    _results.append({'source': source, 'status': 'skipped', 'count': 0,
                     'attempts': [], 'reason': reason})


def track_source(source):
    """Keep public list/tuple APIs unchanged while capturing each HTTP attempt."""
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            attempts = []
            token = _attempts.set(attempts)
            name = source(*args, **kwargs) if callable(source) else source
            count = 0
            raised = None
            try:
                result = fn(*args, **kwargs)
                count = len(result[0] if isinstance(result, tuple) else result)
                return result
            except Exception as exc:
                raised = type(exc).__name__
                raise
            finally:
                _attempts.reset(token)
                good = sum(a['status'] == 'ok' for a in attempts)
                bad = len(attempts) - good
                status = ('failed' if raised or not good else
                          'partial' if bad else 'ok' if count else 'empty')
                item = {'source': name, 'status': status, 'count': count,
                        'attempts': attempts}
                if raised:
                    item['reason'] = raised
                _results.append(item)
        return wrapped
    return decorate
