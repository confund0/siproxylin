#!/usr/bin/env python3
"""Scenario runner for drunk_xmpp feature tests on the local Prosody.

Starts the test Prosody (tmp/prosody/), one tests/test-drunk-xmpp.py --json
process per account, runs the steps in order, prints one line per step
and stops everything at the end (also on error or Ctrl-C).

Run (from the repo root, one Bash command):
    venv/bin/python tests/drunk_scenario.py tests/scenarios/direct.yaml [more.yaml ...]
Options:
    --all            print every step (default: only FAIL, XFAIL, XPASS)
    --step-verbose   print the full reply or event under each step
    --reset          delete tmp/scenario-state/ (kept OMEMO keys) and the
                     OMEMO PEP items of all test users before the first
                     scenario (Prosody stopped). Use it when the keys and
                     the device lists on the server no longer fit.
OMEMO keys: each account keeps its keys between runs in
tmp/scenario-state/<account>/keys.json, like an app account (one device per
account name; alice and alice2 are two devices). fresh_keys: true gives the
account new keys in the run dir (a new device each run).
Output: one line per step that did not pass (FAIL, XFAIL, XPASS; with
--all also PASS), a summary line and the run dir tmp/scenario-runs/<scenario>-<time>/ (per account: .out stdout
JSON, .err stderr, logs-<account>/, .conf; steps.jsonl with all results;
console.txt with all step lines, also the PASS lines).
Exit code 0 when no step FAILs, 2 when a Prosody already runs (nothing
is written then). tmp/prosody/data is not wiped.

Scenario file (YAML):
    accounts:                  # name: options; default jid
      alice: {}                # <name>@localhost/<name>, password <user>pass;
      bob: {}                  # verbose: true gives logs in every reply (for
      alice2: {jid: alice@localhost/second}   # log_text checks)
      carol: {fresh_keys: true}  # new OMEMO keys (new device) each run
    rooms:                     # written to Prosody storage before start
      enc: {owner: alice, members: [bob], members_only: true, whois: anyone}
      pw: {owner: alice, password: secret}   # password: letters, digits, "-", "_", "."
      new: {seed: false}       # only a name, not written
    files:
      f1: {name: small.txt, text: "file content"}
    steps:
      - alice: sendenc ${jid.bob} hello $RUN     # account: command line
        save: m1                                 # save the reply as m1
      - bob: wait message                        # wait for an event
        match: {body: "hello $RUN", encrypted: true}
        timeout: 10                              # default 10 s
        save: ev                                 # save the event as ev
        expect: {id: "${m1.msg_id}"}             # fields of this event
      - expect: {ev.reply_to_id: "${m1.msg_id}"} # fields of saved values
        known_fail: "replyenc sends no reply_to_id"

Values:
    $RUN          unique tag of this run (use it in bodies, so old
                  messages in MAM or offline storage never match)
    ${jid.alice}  bare JID of an account;  ${room.enc}  room JID
                  (node <key>$RUN unless the room sets "node")
    ${file.f1}    path of a file;  ${m1.msg_id}, ${ev.sent.0.id}  saved values
    A missing or null ${...} value fails the step ("unset value").
    Room nodes and file names: letters, digits, "-", "_", "." only
    (file names also a space); no "/" and no "..".
Steps:
    Command: judged by the reply "ok" (only the command's own errors).
    Wait: judged by the event it got; "match" as in the tool ("body~"
    means contains). No default "source": the $RUN tag keeps old
    history out.
    expect keys: "f" equal, "f~" contains, "f!~" does not contain;
    value "*" means present (not null), null means absent (for saved
    values only a literal null, not a ${...} value). Command replies
    also have log_text (log messages joined) and sent_types
    ("iq:set presence:unavailable ...").
    expect_fail: true   the command must give ok=false, or the wait
                        must time out.
    known_fail: "why"   a failure prints XFAIL (not FAIL); a pass XPASS.
    confirm: DELETE     passed to commands that ask for it.
    sleep: 1.5          runner waits (no account).
"""

import argparse
import fcntl
import json
import os
import queue
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / 'tests' / 'test-drunk-xmpp.py'
PY = REPO / 'venv' / 'bin' / 'python'
PROSODY_DIR = REPO / 'tmp' / 'prosody'
PROSODY_DATA = PROSODY_DIR / 'data'
CERT = PROSODY_DIR / 'certs' / 'localhost.crt'
CONF_DIR = PROSODY_DATA / 'conference%2elocalhost'
RUNS_DIR = REPO / 'tmp' / 'scenario-runs'
STATE_DIR = REPO / 'tmp' / 'scenario-state'
DOMAIN = 'localhost'
MUC = 'conference.localhost'
TEST_USERS = ('alice', 'bob', 'carol', 'dave')
OMEMO_NODE_PREFIXES = ('pep_eu%2esiacs%2econversations%2eaxolotl',
                       'pep_urn%3axmpp%3aomemo%3a2%3a')
PID_FILE = PROSODY_DIR / 'prosody.pid'
# Names from the scenario that become paths or go into Prosody storage (Lua)
NODE_RE = re.compile(r'[a-z0-9._-]+')
FILE_RE = re.compile(r'[A-Za-z0-9._ -]+')
BARE_JID_RE = re.compile(r'[a-z0-9._-]+@[a-z0-9.-]+')
PASSWORD_RE = re.compile(r'[A-Za-z0-9._-]+')
WHOIS_VALUES = ('anyone', 'moderators')


class StepError(Exception):
    pass


class ProsodyRunning(Exception):
    """Another Prosody runs: stop the whole run before any storage write."""


def safe_name(name, pattern, what):
    """Return name if it is a plain name (no path), else raise StepError."""
    if not pattern.fullmatch(name) or '..' in name or name.strip() != name:
        raise StepError(f'bad {what} {name!r}: only letters, digits, "-", "_", "."')
    return name


# ------------------------------------------------------------------ Prosody

def check_no_prosody():
    """Raise ProsodyRunning if a Prosody runs; remove a stale pid file."""
    try:
        socket.create_connection(('127.0.0.1', 15222), 0.5).close()
        raise ProsodyRunning('a Prosody already listens on 127.0.0.1:15222;'
                             ' stop it first')
    except OSError:
        pass
    try:
        f = open(PID_FILE, 'r+')
    except FileNotFoundError:
        return
    with f:
        try:
            fcntl.lockf(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ProsodyRunning(f'a Prosody holds the lock on {PID_FILE}'
                                 f' (pid {f.read().strip() or "?"}); stop it first')
        fcntl.lockf(f, fcntl.LOCK_UN)
    PID_FILE.unlink(missing_ok=True)  # no process holds it: stale


class Prosody:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.log = None
        self.proc = None

    def start(self):
        """Start Prosody and wait for its port. self.proc is set before
        the wait, so stop() can end it after an error or a signal."""
        self.log = open(self.run_dir / 'prosody.out', 'w')
        self.proc = subprocess.Popen(
            ['prosody', '--config', str(PROSODY_DIR / 'prosody.cfg.lua')],
            stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(100):
            if self.proc.poll() is not None:
                self.stop()
                raise RuntimeError(f'Prosody exited with code {self.proc.returncode}'
                                   f' (see {self.run_dir}/prosody.out)')
            try:
                socket.create_connection(('127.0.0.1', 15222), 0.2).close()
            except OSError:
                time.sleep(0.1)
                continue
            if self.proc.poll() is not None:  # the port is from another process
                self.stop()
                raise RuntimeError(f'Prosody exited with code {self.proc.returncode},'
                                   f' but port 15222 answers (see {self.run_dir}/prosody.out)')
            return
        self.stop()
        raise RuntimeError('Prosody did not start')

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.log is not None:
            self.log.close()


def seed_room(node, owner, members, members_only=True, whois='anyone',
              password=None):
    """Write a room to Prosody storage (Prosody stopped).

    The tool has no command to create or configure rooms, and a room made
    by /join stays locked for others (no instant-room config).
    All values go into Lua text: check them first."""
    safe_name(node, NODE_RE, 'room node')
    for jid in [owner] + list(members):
        if not BARE_JID_RE.fullmatch(jid):
            raise StepError(f'bad room member JID {jid!r}')
    if whois not in WHOIS_VALUES:
        raise StepError(f'bad whois {whois!r}: use one of {", ".join(WHOIS_VALUES)}')
    if password is not None:
        safe_name(password, PASSWORD_RE, 'room password')
    lines = ['return {', f'\t["{owner}"] = "owner";']
    lines += [f'\t["{m}"] = "member";' for m in members]
    lines += [f'\t["_jid"] = "{node}@{MUC}";',
              '\t["_affiliation_data"] = {};',
              '\t["_data"] = {',
              '\t\t["persistent"] = true;',
              f'\t\t["members_only"] = {"true" if members_only else "false"};',
              f'\t\t["whois"] = "{whois}";',
              '\t\t["hidden"] = true;']
    if password is not None:
        lines.append(f'\t\t["password"] = "{password}";')
    lines += ['\t};', '};', '']
    (CONF_DIR / 'config').mkdir(parents=True, exist_ok=True)
    (CONF_DIR / 'config' / f'{node}.dat').write_text('\n'.join(lines))
    pers = CONF_DIR / 'persistent.dat'
    text = pers.read_text() if pers.exists() else 'return {\n};\n'
    key = f'["{node}@{MUC}"]'
    if key not in text:
        text = text.rstrip().rstrip('};').rstrip() + f'\n\t{key} = true;\n}};\n'
        pers.write_text(text)


BUNDLE_ENTRY_RE = re.compile(
    r'^\t\["eu\.siacs\.conversations\.axolotl\.bundles:\d+"\] = \{\n.*?^\t\};\n',
    re.M | re.S)


def clean_omemo_pep(users):
    """Delete the OMEMO PEP items of users (Prosody stopped).

    Used by --reset: new keys publish new devices; without this the
    device lists keep the old devices. Old bundle nodes are also removed from
    pep/<user>.dat. Other PEP nodes (bookmarks, ...) stay."""
    host = PROSODY_DATA / 'localhost'
    for node_dir in host.glob('pep_*'):
        if not node_dir.name.startswith(OMEMO_NODE_PREFIXES):
            continue
        for user in users:
            for ext in ('.list', '.lidx'):
                (node_dir / f'{user}{ext}').unlink(missing_ok=True)
        if 'bundles%3a' in node_dir.name and not any(node_dir.iterdir()):
            node_dir.rmdir()  # empty legacy bundle node of an old device
    for user in users:
        dat = host / 'pep' / f'{user}.dat'
        if dat.exists():
            text = dat.read_text()
            new = BUNDLE_ENTRY_RE.sub('', text)
            if new != text:
                dat.write_text(new)


def reset_state():
    """--reset: delete the kept keys and the OMEMO PEP items (Prosody stopped)."""
    check_no_prosody()  # before any write to Prosody storage
    if STATE_DIR.is_dir():
        shutil.rmtree(STATE_DIR)
    clean_omemo_pep(TEST_USERS)
    print(f'== reset: deleted {STATE_DIR.relative_to(REPO)}/ content and OMEMO PEP items'
          f' of {", ".join(TEST_USERS)}', flush=True)


# ------------------------------------------------------------------ clients

class Client:
    def __init__(self, run_dir, name, conf, verbose=False):
        self.name = name
        self.bad = []          # stdout lines that are not JSON
        self.q = queue.Queue()
        self.n = 0
        env = dict(os.environ)
        env['SSL_CERT_FILE'] = str(CERT)
        self.out = open(run_dir / f'{name}.out', 'w')
        self.err = open(run_dir / f'{name}.err', 'w')
        self.proc = subprocess.Popen(
            [str(PY), str(TOOL), '--json', '--config', str(conf),
             '--log-dir', str(run_dir / f'logs-{name}')] + (['--verbose'] if verbose else []),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.err,
            text=True, cwd=str(run_dir), env=env, bufsize=1,
            start_new_session=True)  # Ctrl-C goes to the runner only
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in self.proc.stdout:
            self.out.write(line)
            self.out.flush()
            try:
                obj = json.loads(line)
            except ValueError:
                self.bad.append(line.rstrip('\n'))
                continue
            if isinstance(obj, dict) and obj.get('done'):
                self.q.put(obj)
        self.q.put(None)

    def request(self, req, timeout):
        """Send one request, return its reply (None on timeout or exit)."""
        self.n += 1
        rid = f'{self.name}-{self.n}'
        req = dict(req, id=rid)
        try:
            self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + '\n')
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            return None
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                return None
            try:
                rep = self.q.get(timeout=left)
            except queue.Empty:
                return None
            if rep is None:
                self.q.put(None)
                return None
            if rep.get('id') == rid:
                return rep

    def quit(self):
        try:
            self.proc.stdin.write('{"id":"quit","cmd":"quit"}\n')
            self.proc.stdin.flush()
            self.proc.stdin.close()
        except (BrokenPipeError, ValueError, OSError):
            pass
        try:
            self.proc.wait(15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.out.close()
        self.err.close()
        return self.proc.returncode


def write_conf(path, jid, password, omemo_dir):
    conf = {
        'xmpp': {
            'jid': jid, 'password': password,
            'server': '127.0.0.1', 'port': 15222, 'ca_certs': str(CERT),
            'reconnect_max_delay': 30, 'keepalive_interval': 60,
            'omemo': {'enabled': True, 'storage_path': str(omemo_dir / 'keys.json')},
            'rooms': None,
        },
        'logging': {'level': 'INFO', 'console': {'enabled': True},
                    'file': {'enabled': False}, 'xml': {'enabled': False}},
    }
    path.write_text(yaml.safe_dump(conf, sort_keys=False))


# ------------------------------------------------------------------ values

VAR_RE = re.compile(r'\$\{([^}]+)\}|\$RUN\b')


def lookup(values, ref, allow_none=False):
    """Value of name.path. A missing path or a null value raises
    StepError, unless allow_none (then it gives None)."""
    parts = ref.strip().split('.')
    if parts[0] not in values:
        raise StepError(f'unknown value ${{{ref}}}')
    cur = values[parts[0]]
    for p in parts[1:]:
        if isinstance(cur, list):
            try:
                cur = cur[int(p)]
            except (ValueError, IndexError):
                cur = None
        elif isinstance(cur, dict):
            cur = cur.get(p)
        else:
            cur = None
        if cur is None:
            break
    if cur is None and not allow_none:
        raise StepError(f'unset value ${{{ref}}}')
    return cur


def subst(obj, values):
    """Replace $RUN and ${name.path} in strings (also in lists and dicts)."""
    if isinstance(obj, str):
        m = VAR_RE.fullmatch(obj)
        if m and m.group(1):
            return lookup(values, m.group(1))  # keep the type (bool, null, list)

        def one(m):
            if m.group(1) is None:
                return values['RUN']
            return str(lookup(values, m.group(1)))
        return VAR_RE.sub(one, obj)
    if isinstance(obj, list):
        return [subst(x, values) for x in obj]
    if isinstance(obj, dict):
        return {subst(k, values): subst(v, values) for k, v in obj.items()}
    return obj


def check_expect(expect, get):
    """Return a list of mismatch texts. get(key) returns the value."""
    bad = []
    for key, want in expect.items():
        key = str(key)
        if key.endswith('!~'):
            have = get(key[:-2])
            if have is not None and str(want) in str(have):
                bad.append(f'{key[:-2]} contains {want!r}')
        elif key.endswith('~'):
            have = get(key[:-1])
            if have is None or str(want) not in str(have):
                bad.append(f'{key[:-1]}: want ~{want!r} got {short(have)}')
        elif want == '*':
            if get(key) is None:
                bad.append(f'{key}: want present got None')
        else:
            have = get(key)
            if have != want and not (isinstance(want, str) and str(have) == want):
                bad.append(f'{key}: want {want!r} got {short(have)}')
    return bad


def short(v, n=80):
    s = repr(v)
    return s if len(s) <= n else s[:n] + '...'


def with_derived(rep):
    rep = dict(rep)
    rep['log_text'] = '\n'.join(l.get('msg', '') for l in rep.get('logs') or [])
    rep['sent_types'] = ' '.join(f"{s.get('kind')}:{s.get('type')}" for s in rep.get('sent') or [])
    return rep


def hms(t):
    """'YYYY-MM-DD HH:MM:SS.mmm' -> 'HH:MM:SS' (for siplog --since/--until)."""
    return t.split(' ')[-1].split('.')[0] if t else None


# ------------------------------------------------------------------ run

class Scenario:
    def __init__(self, path: Path, opts):
        self.path = path
        self.opts = opts
        self.name = path.stem
        self.run_tag = 'r' + time.strftime('%H%M%S') + secrets.token_hex(2)
        self.dir = RUNS_DIR / f'{self.name}-{time.strftime("%Y%m%d-%H%M%S")}'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.clients = {}
        self.ready_reps = {}   # account -> "ready" reply taken in setup()
        self.prosody = None
        self.counts = {'PASS': 0, 'FAIL': 0, 'XFAIL': 0, 'XPASS': 0}
        self.console = []
        self.steps_log = open(self.dir / 'steps.jsonl', 'w')
        self.last_t_start = None

    def out(self, line, show=True):
        """Print a line (if show) and keep it for console.txt."""
        if show:
            print(line, flush=True)
        self.console.append(line)

    def setup(self):
        accounts = self.data.get('accounts') or {}
        if not accounts:
            raise StepError('no accounts')
        for name in accounts:  # names become paths: check before any write
            safe_name(str(name), NODE_RE, 'account name')
        check_no_prosody()  # before any write to Prosody storage
        self.values = {'RUN': self.run_tag, 'jid': {}, 'room': {}, 'file': {}}
        self.accounts = {}
        for name, a in accounts.items():
            a = a or {}
            jid = a.get('jid') or f'{name}@{DOMAIN}/{name}'
            bare = jid.split('/')[0]
            user = bare.split('@')[0]
            self.accounts[name] = dict(a, jid=jid, bare=bare, user=user,
                                       password=a.get('password') or f'{user}pass')
            self.values['jid'][name] = bare
        for key, r in (self.data.get('rooms') or {}).items():
            r = r or {}
            node = subst(str(r.get('node') or f'{key}$RUN'), self.values).lower()
            safe_name(node, NODE_RE, 'room node')
            self.values['room'][key] = f'{node}@{MUC}'
            if r.get('seed', True):
                owner = self.values['jid'][r['owner']]
                members = [self.values['jid'][m] for m in r.get('members') or []]
                password = r.get('password')
                seed_room(node, owner, members, bool(r.get('members_only', True)),
                          r.get('whois', 'anyone'),
                          None if password is None else str(password))
        for key, f in (self.data.get('files') or {}).items():
            name = subst(str(f.get('name', key)), self.values)
            p = self.dir / 'files' / safe_name(name, FILE_RE, 'file name')
            p.parent.mkdir(exist_ok=True)
            p.write_text(subst(f.get('text', 'scenario file\n'), self.values))
            self.values['file'][key] = str(p)
        self.prosody = Prosody(self.dir)
        self.prosody.start()
        last_by_bare = {}  # bare JID -> name of the last started account
        for name, a in self.accounts.items():
            prev = last_by_bare.get(a['bare'])
            if prev is not None:
                # same bare JID: OMEMO device list publishes race; start after "ready"
                self.ready_reps[prev] = self.wait_ready(self.clients[prev])
            last_by_bare[a['bare']] = name
            if a.get('fresh_keys'):
                keys = self.dir / f'{name}-omemo'
            else:
                keys = STATE_DIR / name
            keys.mkdir(parents=True, exist_ok=True)
            conf = self.dir / f'{name}.conf'
            write_conf(conf, a['jid'], a['password'], keys)
            self.clients[name] = Client(self.dir, name, conf, bool(a.get('verbose')))

    @staticmethod
    def wait_ready(c):
        return c.request({'cmd': 'wait', 'event': 'ready', 'match': {}, 'timeout': 60}, 70)

    def ready(self):
        t0 = time.time()
        bad = []
        for name, c in self.clients.items():
            if name in self.ready_reps:
                rep = self.ready_reps[name]  # taken in setup()
            else:
                rep = self.wait_ready(c)
            ev = (rep or {}).get('event') or {}
            if not (rep and rep.get('ok') and ev.get('omemo_ready')):
                if rep is None:
                    why = f'no reply (exit code {c.proc.poll()})'
                else:
                    why = rep.get('error') or (json.dumps(ev) if ev else 'no event')
                bad.append(f'{name}: {why}')
        status = 'FAIL' if bad else 'PASS'
        self.counts[status] += 1
        self.out(f'{status} 0 ready {" ".join(self.clients)} {time.time() - t0:.2f}s'
                 + (': ' + '; '.join(bad) if bad else ''), self.opts.all or bool(bad))
        return not bad

    def run_step(self, i, step):
        t0 = time.time()
        known = step.get('known_fail')
        expect_fail = bool(step.get('expect_fail'))
        label, reason, ids, rep, result = '', None, {}, None, None
        acct = next((k for k in step if k in self.clients), None)
        try:
            if acct is not None:
                label = acct  # kept when the line has an unset value
                line = subst(str(step[acct]), self.values).strip()
                cmd, _, rest = line.partition(' ')
                label = f'{acct} {cmd}'
                c = self.clients[acct]
                if cmd == 'wait':
                    event = rest.strip()
                    label += f' {event}'
                    timeout = float(step.get('timeout', 10))
                    req = {'cmd': 'wait', 'event': event,
                           'match': subst(step.get('match') or {}, self.values),
                           'timeout': timeout}
                    rep = c.request(req, timeout + 15)
                    result = (rep or {}).get('event')
                    if rep is None:
                        reason = f'no reply (exit code {c.proc.poll()})'
                    elif expect_fail:
                        if result:
                            reason = 'got an event, want none'
                    elif not result:
                        reason = rep.get('error') or 'no event'
                    if result:
                        ids = {k: result.get(k) for k in ('id', 'origin_id', 'stanza_id')
                               if result.get(k)}
                else:
                    req = {'cmd': cmd, 'args': [rest] if rest else []}
                    if 'confirm' in step:
                        req['confirm'] = step['confirm']
                    rep = c.request(req, float(step.get('timeout', 60)))
                    if rep is None:
                        reason = f'no reply (exit code {c.proc.poll()})'
                    else:
                        result = with_derived(rep)
                        if expect_fail and rep.get('ok'):
                            reason = 'ok=true, want ok=false'
                        elif not expect_fail and not rep.get('ok'):
                            reason = rep.get('error') or 'ok=false'
                        if rep.get('msg_id'):
                            ids = {'msg_id': rep['msg_id']}
                if reason is None and step.get('expect') and result is not None:
                    exp = subst(step['expect'], self.values)
                    bad = check_expect(exp, result.get)
                    if bad:
                        reason = '; '.join(bad)
                if step.get('save') and result is not None:
                    self.values[step['save']] = result
            elif 'expect' in step:
                label = 'expect'
                exp = {}
                for k, v in step['expect'].items():
                    exp[str(k)] = subst(v, self.values)

                def get(key):
                    # a literal null in the step allows a missing value
                    return lookup(self.values, key,
                                  allow_none=key in exp and exp[key] is None)
                bad = check_expect(exp, get)
                if bad:
                    reason = '; '.join(bad)
            elif 'sleep' in step:
                label = f'sleep {step["sleep"]}'
                time.sleep(float(step['sleep']))
            else:
                raise StepError(f'unknown step {json.dumps(step, ensure_ascii=False)}')
        except StepError as e:
            reason = str(e)
        except Exception as e:  # a runner fault must not stop the other steps
            reason = f'runner error: {type(e).__name__}: {e}'
        secs = time.time() - t0
        if reason is None:
            status = 'XPASS' if known else 'PASS'
        else:
            status = 'XFAIL' if known else 'FAIL'
        self.counts[status] += 1
        t_start = (rep or {}).get('t_start')
        t_end = (rep or {}).get('t_end')
        text = f'{status} {i} {label} {secs:.2f}s'
        if status == 'FAIL':
            text = f'{status} {i} {label}: {reason}'
            extra = [f'{k}={v}' for k, v in ids.items()]
            if t_start:
                extra.append(f't={t_start}..{hms(t_end)}')
            if extra:
                text += f' (ids: {", ".join(extra)})'
            if acct is not None:
                since = hms(self.last_t_start or t_start)
                if since:
                    text += (f'\n     venv/bin/python tools/siplog.py stanzas '
                             f'{self.rel(self.dir / f"logs-{acct}")} --since {since}'
                             f' --until {hms(t_end) or since}')
        elif status == 'XFAIL':
            got = reason if len(reason) <= 70 else '...' + reason[-67:]
            text += f' known: {known} (got: {got})'
        elif status == 'XPASS':
            text += f' (known_fail passed: {known})'
        show = self.opts.all or status != 'PASS'
        self.out(text, show)
        if self.opts.step_verbose and (rep is not None or result is not None):
            self.out('     ' + json.dumps(result if result is not None else rep,
                                         ensure_ascii=False, default=str), show)
        self.steps_log.write(json.dumps({'step': i, 'label': label, 'status': status,
                                         'reason': reason, 'known_fail': known,
                                         'reply': rep}, ensure_ascii=False, default=str) + '\n')
        self.steps_log.flush()
        if t_start:
            self.last_t_start = t_start

    @staticmethod
    def rel(p):
        try:
            return str(p.relative_to(Path.cwd()))
        except ValueError:
            return str(p)

    def finish(self):
        for name, c in self.clients.items():
            code = c.quit()
            bad = []
            if code != 0:
                bad.append(f'exit code {code}')
            if c.bad:
                bad.append(f'{len(c.bad)} stdout lines not JSON: {c.bad[0][:80]!r}')
            if bad:
                self.counts['FAIL'] += 1
                self.out(f'FAIL end {name}: {"; ".join(bad)} (see {name}.err)')
        if self.prosody:
            self.prosody.stop()
        self.steps_log.close()

    def run(self):
        t0 = time.time()
        self.out(f'== {self.name} ({self.rel(self.path)}) RUN={self.run_tag}')
        interrupted = False
        try:
            self.data = yaml.safe_load(self.path.read_text()) or {}
            self.setup()
            if self.ready():
                for i, step in enumerate(self.data.get('steps') or [], 1):
                    self.run_step(i, step)
        except (StepError, RuntimeError, OSError, KeyError, yaml.YAMLError) as e:
            self.counts['FAIL'] += 1
            self.out(f'FAIL setup: {type(e).__name__}: {e}')
        except (AttributeError, TypeError) as e:
            self.counts['FAIL'] += 1
            self.out(f'FAIL bad scenario: {type(e).__name__}: {e}')
        except KeyboardInterrupt:
            interrupted = True
            self.out('== interrupted')
        finally:
            # a second Ctrl-C must not stop the cleanup
            old = [signal.signal(s, signal.SIG_IGN) for s in (signal.SIGINT, signal.SIGTERM)]
            try:
                self.finish()
            finally:
                signal.signal(signal.SIGINT, old[0])
                signal.signal(signal.SIGTERM, old[1])
        c = self.counts
        self.out(f'== {self.name}: {c["PASS"]} PASS, {c["FAIL"]} FAIL, {c["XFAIL"]} XFAIL, '
                 f'{c["XPASS"]} XPASS in {time.time() - t0:.1f}s')
        self.out(f'   run dir: {self.rel(self.dir)}')
        (self.dir / 'console.txt').write_text('\n'.join(self.console) + '\n')
        if interrupted:
            raise KeyboardInterrupt
        return c['FAIL'] == 0


def on_term(signum, frame):
    raise KeyboardInterrupt


def main():
    ap = argparse.ArgumentParser(description='Run drunk_xmpp scenarios on the local Prosody.')
    ap.add_argument('scenarios', nargs='+', help='scenario YAML files')
    ap.add_argument('--all', action='store_true',
                    help='print every step (default: only FAIL, XFAIL, XPASS)')
    ap.add_argument('--step-verbose', action='store_true', help='print full replies and events')
    ap.add_argument('--reset', action='store_true',
                    help='delete tmp/scenario-state/ and the OMEMO PEP items of the test users')
    opts = ap.parse_args()
    signal.signal(signal.SIGTERM, on_term)
    ok = True
    t0 = time.time()
    try:
        if opts.reset:
            reset_state()
        for path in opts.scenarios:
            ok &= Scenario(Path(path).resolve(), opts).run()
    except KeyboardInterrupt:
        return 130
    except ProsodyRunning as e:
        print(f'== abort: {e}', flush=True)
        return 2
    if len(opts.scenarios) > 1:
        print(f'== all: {"no FAIL" if ok else "FAIL"} in {time.time() - t0:.1f}s', flush=True)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
