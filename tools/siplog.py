#!/usr/bin/env python3
"""Read-only parser for Siproxylin logs.

Subcommands:
  stanzas  filter SEND/RECV stanzas in xmpp-protocol.log
  mam      pair MAM queries with their results and fin or error
  merge    merge main.log, account-*-app.log and xmpp-protocol.log by time

Input: a log dir or one file (default ./tmp/logs/).
Stdlib only. Never writes files.
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

PROTO_RE = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) - (.*)$')
APP_RE = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) - (\S+) - '
                    r'(DEBUG|INFO|WARNING|ERROR|CRITICAL) - (.*)$')
LEVELS = {'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50}
MAM_NS = 'urn:xmpp:mam:2'
RSM_NS = 'http://jabber.org/protocol/rsm'
OMEMO_NS = ('eu.siacs.conversations.axolotl', 'urn:xmpp:omemo:2')
DEFAULT_DIR = Path('tmp/logs')


# ------------------------------------------------------------------ files

def rotated(path):
    """Return path and its rotated files (.5 .. .1), oldest first."""
    olds = [path.with_name(f'{path.name}.{i}') for i in range(5, 0, -1)]
    return [p for p in olds + [path] if p.is_file()]


def read_lines(paths):
    for p in paths:
        with open(p, encoding='utf-8', errors='replace') as f:
            for line in f:
                yield line.rstrip('\n')


def proto_files(path):
    path = Path(path)
    if path.is_dir():
        return rotated(path / 'xmpp-protocol.log')
    return [path]


def source_name(name):
    """Short source name for a log file name."""
    m = re.match(r'account-(\d+)-app\.log', name)
    if m:
        return 'acc' + m.group(1)
    return 'proto' if name.startswith('xmpp-protocol') else 'main'


# ------------------------------------------------------------------ parsing

def local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def ns(tag):
    return tag[1:].split('}', 1)[0] if tag.startswith('{') else ''


def parse_proto(lines):
    """Parse xmpp-protocol.log lines into entry dicts.

    Keys: time (datetime), dir ('SEND', 'RECV' or None), text, raw
    (XML text or None), el (Element or None), lines (source lines).
    """
    out, cur = [], None
    for line in lines:
        m = PROTO_RE.match(line)
        if m:
            cur = {'time': datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S,%f'),
                   'text': m.group(2), 'lines': [line]}
            out.append(cur)
        elif cur is not None:
            cur['text'] += '\n' + line
            cur['lines'].append(line)
    for e in out:
        d, _, rest = e['text'].partition(': ')
        e['dir'] = d if d in ('SEND', 'RECV') else None
        e['raw'] = rest if e['dir'] else None
        e['el'] = None
        if e['raw']:
            try:
                e['el'] = ET.fromstring(e['raw'])
            except ET.ParseError:
                pass  # stream header, features, broken XML
    return out


def parse_app(lines, source='main'):
    """Parse main.log or account-N-app.log lines into entry dicts.

    Keys: time (datetime, no ms), logger, level, text, lines, source.
    """
    out, cur = [], None
    for line in lines:
        m = APP_RE.match(line)
        if m:
            cur = {'time': datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S'),
                   'logger': m.group(2), 'level': m.group(3),
                   'text': m.group(4), 'lines': [line], 'source': source}
            out.append(cur)
        elif cur is not None:
            cur['text'] += '\n' + line
            cur['lines'].append(line)
    return out


def kind(e):
    if e['el'] is not None:
        name = local(e['el'].tag)
    else:
        m = re.match(r'<([\w:.-]+)', e['raw'] or '')
        name = m.group(1) if m else ''
    return name if name in ('message', 'iq', 'presence') else 'other'


def ids(el):
    """All id and queryid attribute values in the tree."""
    return {v for x in el.iter() for k, v in x.attrib.items() if k in ('id', 'queryid')}


def jid_match(want, value):
    if not value:
        return False
    if '/' in want:
        return want == value
    return want == value.split('/', 1)[0]


# ------------------------------------------------------------------ filters

WHEN_RE = re.compile(r'(?:(?P<date>\d{4}-\d\d-\d\d)(?:[ T]|$))?'
                     r'(?:(?P<h>\d\d):(?P<m>\d\d)(?::(?P<s>\d\d)(?:[.,](?P<f>\d{1,6}))?)?)?'
                     r'(?P<tz>Z|[+-]\d\d(?::?\d\d)?)?')


def parse_when(s, end=False):
    """Parse a --since or --until value.

    'HH:MM[:SS[.fff]]' gives a time of day (timedelta from midnight),
    a value with a date gives a datetime. A zone offset is dropped: logs
    use local times. With end=True the bound covers the whole unit given
    (a second, a minute, a day) and is exclusive.
    """
    if s is None:
        return None
    m = WHEN_RE.fullmatch(s.strip())
    if not m or not (m['date'] or m['h']):
        raise argparse.ArgumentTypeError(
            f"bad time {s!r}: use 'HH:MM[:SS[.fff]]' or 'YYYY-MM-DD[ HH:MM[:SS[.fff]]]'")
    frac = m['f'] or ''
    try:
        tod = dtime(int(m['h'] or 0), int(m['m'] or 0), int(m['s'] or 0),
                    int(frac.ljust(6, '0')) if frac else 0)
        day = datetime.strptime(m['date'], '%Y-%m-%d') if m['date'] else None
    except ValueError as e:
        raise argparse.ArgumentTypeError(f'bad time {s!r}: {e}') from None
    if frac:
        step = timedelta(microseconds=10 ** (6 - len(frac)))
    elif m['s']:
        step = timedelta(seconds=1)
    elif m['h']:
        step = timedelta(minutes=1)
    else:
        step = timedelta(days=1)
    if day is None:
        b = datetime.combine(datetime.min, tod) - datetime.min
    else:
        b = datetime.combine(day, tod)
    return b + step if end else b


def parse_since(s):
    return parse_when(s)


def parse_until(s):
    return parse_when(s, end=True)


def in_range(t, since, until):
    """since is inclusive, until is exclusive (see parse_when)."""
    def key(b):
        if isinstance(b, timedelta):
            return t - datetime.combine(t.date(), dtime())
        return t
    if since is not None and key(since) < since:
        return False
    if until is not None and key(until) >= until:
        return False
    return True


def stanza_filter(entries, jid=None, sid=None, kind_=None, since=None,
                  until=None, grep=None):
    """Keep SEND/RECV entries that match all given filters."""
    out = []
    for e in entries:
        if not e['dir'] or not in_range(e['time'], since, until):
            continue
        if kind_ and kind(e) != kind_:
            continue
        if grep and grep not in e['raw']:
            continue
        el = e['el']
        if jid and (el is None or not (jid_match(jid, el.get('to'))
                                       or jid_match(jid, el.get('from')))):
            continue
        if sid and (sid not in ids(el) if el is not None else sid not in e['raw']):
            continue
        out.append(e)
    return out


def header(e):
    el = e['el']
    ts = e['time'].strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    if el is None:
        return f'{ts} {e["dir"]} {kind(e)} (unparsed)'
    return (f'{ts} {e["dir"]} {kind(e)} type={el.get("type", "-")} '
            f'id={el.get("id", "-")} {el.get("from", "-")}->{el.get("to", "-")}')


# ------------------------------------------------------------------ MAM

def child(el, name, namespace=None):
    for c in el:
        if local(c.tag) == name and (namespace is None or ns(c.tag) == namespace):
            return c
    return None


def form_value(query, var):
    for f in query.iter():
        if local(f.tag) == 'field' and f.get('var') == var:
            v = child(f, 'value')
            return v.text if v is not None else ''
    return None


def rsm_value(parent, name):
    s = child(parent, 'set', RSM_NS) if parent is not None else None
    c = child(s, name) if s is not None else None
    if c is None:
        return None
    return c.text or ''


def inner_message(result):
    fwd = child(result, 'forwarded')
    return child(fwd, 'message') if fwd is not None else None


def mam_queries(entries):
    """Pair MAM queries with result messages and fin/error iq.

    Returns a list of dicts: time, iq_id, queryid, to, with, start, before,
    after, max, results (list of (archive id, inner message Element)),
    end (entry or None), complete, first, last, count, error.
    """
    queries, by_iq, by_qid = [], {}, {}
    for e in entries:
        el = e['el']
        if el is None:
            continue
        tag = local(el.tag)
        if e['dir'] == 'SEND' and tag == 'iq' and el.get('type') == 'set':
            q = child(el, 'query', MAM_NS)
            if q is None:
                continue
            item = {'time': e['time'], 'iq_id': el.get('id'),
                    'queryid': q.get('queryid'), 'to': el.get('to'),
                    'with': form_value(q, 'with'), 'start': form_value(q, 'start'),
                    'before': rsm_value(q, 'before'), 'after': rsm_value(q, 'after'),
                    'max': rsm_value(q, 'max'), 'results': [], 'end': None,
                    'complete': False, 'first': None, 'last': None,
                    'count': None, 'error': None}
            queries.append(item)
            by_iq[item['iq_id']] = item
            if item['queryid']:
                by_qid[item['queryid']] = item
        elif e['dir'] == 'RECV' and tag == 'message':
            r = child(el, 'result', MAM_NS)
            if r is not None and r.get('queryid') in by_qid:
                by_qid[r.get('queryid')]['results'].append((r.get('id'), inner_message(r)))
        elif e['dir'] == 'RECV' and tag == 'iq' and el.get('id') in by_iq:
            item = by_iq[el.get('id')]
            if item['end'] is not None:
                continue
            item['end'] = e
            fin = child(el, 'fin', MAM_NS)
            if fin is not None:
                item['complete'] = fin.get('complete') in ('true', '1')
                item['first'] = rsm_value(fin, 'first')
                item['last'] = rsm_value(fin, 'last')
                item['count'] = rsm_value(fin, 'count')
            err = child(el, 'error')
            if el.get('type') == 'error':
                conds = [local(c.tag) for c in err if local(c.tag) != 'text'] if err is not None else []
                item['error'] = conds[0] if conds else 'unknown'
    return queries


def body_kind(msg):
    if msg is None:
        return 'none'
    for x in msg.iter():
        if ns(x.tag) in OMEMO_NS and local(x.tag) == 'encrypted':
            return 'omemo'
    return 'plain' if child(msg, 'body') is not None else 'no-body'


def mam_line(q):
    ts = q['time'].strftime('%H:%M:%S.%f')[:-3]
    parts = [ts, f'iq={q["iq_id"]}', f'to={q["to"] or "(own)"}',
             f'with={q["with"] or "-"}']
    for k in ('start', 'before', 'after', 'max'):
        if q[k] is not None:
            parts.append(f'{k}={q[k] or "(empty)"}')
    parts.append(f'results={len(q["results"])}')
    if q['end'] is None:
        parts.append('NO FIN (timeout?)')
        return ' '.join(parts)
    for k in ('count', 'first', 'last'):
        if q[k] is not None:
            parts.append(f'{k}={q[k] or "-"}')
    dur = (q['end']['time'] - q['time']).total_seconds()
    parts.append(f'complete={str(q["complete"]).lower()} dur={dur:.3f}s')
    if q['error']:
        parts.append(f'ERROR={q["error"]}')
    return ' '.join(parts)


def mam_result_lines(q):
    out = []
    for aid, msg in q['results']:
        if msg is None:
            out.append(f'    {aid} (no message)')
            continue
        oid = next((x.get('id') for x in msg.iter() if local(x.tag) == 'origin-id'), None)
        out.append(f'    {aid} from={msg.get("from", "-")} id={msg.get("id", "-")} '
                   f'origin-id={oid or "-"} {body_kind(msg)}')
    return out


# ------------------------------------------------------------------ merge

def merge_sources(path):
    """List of (source name, [files]) in output order."""
    path = Path(path)
    if not path.is_dir():
        return [(source_name(path.name), [path])]
    srcs = [('main', rotated(path / 'main.log'))]
    accs = sorted(path.glob('account-*-app.log'),
                  key=lambda p: int(re.findall(r'\d+', p.name)[0]))
    srcs += [(source_name(p.name), rotated(p)) for p in accs]
    srcs.append(('proto', rotated(path / 'xmpp-protocol.log')))
    return [(n, f) for n, f in srcs if f]


def merge_entries(sources, since=None, until=None, level=None, logger=None,
                  grep=None, no_proto=False):
    """Merge parsed entries by second.

    sources: list of (source name, lines). Sort key is (second, source
    order, place in file): app logs have no ms, so order in one second is
    not exact between sources.
    """
    rows = []
    min_level = LEVELS[level] if level else 0
    for rank, (name, lines) in enumerate(sources):
        proto = name == 'proto'
        if proto and no_proto:
            continue
        entries = parse_proto(lines) if proto else parse_app(lines, name)
        for seq, e in enumerate(entries):
            if not in_range(e['time'], since, until):
                continue
            if not proto:
                if LEVELS[e['level']] < min_level:
                    continue
                if logger and logger not in e['logger']:
                    continue
            if grep and grep not in e['text']:
                continue
            rows.append((e['time'].replace(microsecond=0), rank, seq, name, e))
    rows.sort(key=lambda r: r[:3])
    return [(r[3], r[4]) for r in rows]


# ------------------------------------------------------------------ CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description='Read-only parser for Siproxylin logs.')
    sub = ap.add_subparsers(dest='cmd', required=True)

    def common(p):
        p.add_argument('path', nargs='?', default=str(DEFAULT_DIR),
                       help='log dir or one file (default: tmp/logs/)')
        p.add_argument('--since', type=parse_since,
                       help="'HH:MM[:SS[.fff]]' (any date) or 'YYYY-MM-DD[ HH:MM[:SS[.fff]]]'")
        p.add_argument('--until', type=parse_until,
                       help='same forms as --since; covers the whole second, minute or day given')
        p.add_argument('--grep', help='plain text the entry must contain')

    p = sub.add_parser('stanzas', help='filter SEND/RECV stanzas')
    common(p)
    p.add_argument('--jid', help='match to/from; a bare JID also matches full JIDs')
    p.add_argument('--id', help='stanza id, iq id, queryid, archive id or origin-id')
    p.add_argument('--kind', choices=['message', 'iq', 'presence', 'other'])
    p.add_argument('--short', action='store_true', help='header lines only')

    p = sub.add_parser('mam', help='MAM queries with results and fin or error')
    common(p)
    p.add_argument('--verbose', '-v', action='store_true',
                   help='list archive ids and inner messages')

    p = sub.add_parser('merge', help='merge app logs and protocol log by time')
    common(p)
    p.add_argument('--level', choices=list(LEVELS), help='min level of app log lines')
    p.add_argument('--logger', help='logger name substring (app log lines)')
    p.add_argument('--no-proto', action='store_true', help='leave out protocol lines')

    a = ap.parse_args(argv)
    if not Path(a.path).exists():
        print(f'not found: {a.path}', file=sys.stderr)
        return 1
    since, until = a.since, a.until

    if a.cmd == 'merge':
        srcs = [(n, list(read_lines(f))) for n, f in merge_sources(a.path)]
        if not srcs:
            print(f'no log files in {a.path}', file=sys.stderr)
            return 1
        for name, e in merge_entries(srcs, since, until, a.level, a.logger,
                                     a.grep, a.no_proto):
            for line in e['lines']:
                print(f'{name:6} {line}')
        return 0

    files = proto_files(a.path)
    if not files:
        print(f'no xmpp-protocol.log in {a.path}', file=sys.stderr)
        return 1
    entries = parse_proto(read_lines(files))
    if a.cmd == 'stanzas':
        for e in stanza_filter(entries, a.jid, a.id, a.kind, since, until, a.grep):
            print(header(e))
            if not a.short:
                print(e['raw'])
    else:
        # Pair first, then filter by query send time: a fin after --until
        # still belongs to its query.
        for q in mam_queries(entries):
            if not in_range(q['time'], since, until):
                continue
            line = mam_line(q)
            if a.grep and a.grep not in line:
                continue
            print(line)
            if a.verbose:
                print('\n'.join(mam_result_lines(q)) or '    (no results)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
