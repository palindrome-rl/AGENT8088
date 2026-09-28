"""Conservative tool recovery and local, explicitly qualified usage reporting."""
import json
import math
from pathlib import Path


def tool_error(code, message, action, *, recoverable=False):
    return 'Error: ' + message + '\n' + json.dumps({
        'code': code, 'recoverable': recoverable, 'suggested_action': action,
    }, ensure_ascii=False)


def read_signature(name, args, resolve, permission):
    """Only plain file reads; no writes, shell, polling or remote operations."""
    if name != 'read_text' or not isinstance(args, dict):
        return None
    try:
        path = resolve(args.get('filename', ''))
        if not path.is_file():
            return None
        stat = path.stat()
        return (name, json.dumps(args, sort_keys=True), str(path.resolve()),
                stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, permission)
    except (OSError, ValueError, TypeError):
        return None


def telemetry_summary(path, task_id=None, max_bytes=16 * 1024 * 1024):
    """Bounded tail reader. Unknown costs are never silently counted as free."""
    path = Path(path)
    result = dict(calls=0, errors=0, known_cost_usd=0.0, unknown_cost_calls=0,
                  estimated_cost_calls=0, latency_ms=0, malformed_records=0,
                  # Routing is time the turn spent before any model was called.
                  # Counted apart from model calls: it is real cost, but it is
                  # not a billed request and must not inflate the call count.
                  routing_decisions=0, routing_applied=0, routing_latency_ms=0,
                  truncated=False, tasks={}, available=path.exists())
    if not path.exists():
        return result
    with path.open('rb') as stream:
        size = path.stat().st_size
        if size > max_bytes:
            stream.seek(size - max_bytes)
            stream.readline()
            result['truncated'] = True
        for line in stream.read(max_bytes).splitlines():
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError('expected object')
                if record.get('event') == 'routing_decision':
                    if task_id and (record.get('task_id') or 'unattributed') != task_id:
                        continue
                    result['routing_decisions'] += 1
                    result['routing_applied'] += bool(record.get('applied'))
                    result['routing_latency_ms'] += max(0, int(record.get('latency_ms') or 0))
                    continue
                if record.get('event') != 'model_call':
                    continue
                task = record.get('task_id') or 'unattributed'
                if not isinstance(task, str):
                    raise ValueError('invalid task ID')
                if task_id and task != task_id:
                    continue
                cost = record.get('cost_usd')
                # Historical estimates with missing input usage are incomplete.
                if record.get('input_tokens') is None or record.get('output_tokens') is None:
                    cost = None
                if cost is not None:
                    cost = float(cost)
                    if not math.isfinite(cost) or cost < 0:
                        raise ValueError('invalid cost')
                latency = max(0, float(record.get('latency_ms') or 0))
                if not math.isfinite(latency):
                    raise ValueError('invalid latency')
                result['calls'] += 1
                result['errors'] += record.get('outcome') == 'error'
                result['latency_ms'] += latency
                result['tasks'][task] = result['tasks'].get(task, 0) + 1
                if cost is None:
                    result['unknown_cost_calls'] += 1
                else:
                    result['known_cost_usd'] += cost
                    result['estimated_cost_calls'] += 1
            except (ValueError, TypeError, UnicodeError):
                result['malformed_records'] += 1
    result['known_cost_usd'] = round(result['known_cost_usd'], 8)
    return result
