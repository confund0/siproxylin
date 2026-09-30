"""Offline tests for tools/siplog.py with synthetic log lines."""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
import siplog  # noqa: E402

PROTO = [
    "2026-01-02 10:00:00,100 - SEND: <stream:stream to='example.org' "
    "xmlns:stream='http://etherx.jabber.org/streams' xmlns='jabber:client' version='1.0'>",
    '2026-01-02 10:00:00,200 - Event triggered: connected',
    # MAM query 1: own archive, answered with two results and fin
    '2026-01-02 10:00:01,000 - SEND: <iq id="q1" type="set"><query xmlns="urn:xmpp:mam:2" '
    'queryid="q1"><x xmlns="jabber:x:data" type="submit"><field var="with"><value>'
    'bob@example.org</value></field></x><set xmlns="http://jabber.org/protocol/rsm">'
    '<max>25</max><before /></set></query></iq>',
    '2026-01-02 10:00:01,050 - RECV: <message to="alice@example.org/pc"><result '
    'xmlns="urn:xmpp:mam:2" queryid="q1" id="A1"><forwarded xmlns="urn:xmpp:forward:0">'
    '<message xmlns="jabber:client" from="bob@example.org/x" id="m1"><body>line one',
    'line two</body><origin-id xmlns="urn:xmpp:sid:0" id="o1" /></message></forwarded>'
    '</result></message>',
    '2026-01-02 10:00:01,060 - RECV: <message to="alice@example.org/pc"><result '
    'xmlns="urn:xmpp:mam:2" queryid="q1" id="A2"><forwarded xmlns="urn:xmpp:forward:0">'
    '<message xmlns="jabber:client" from="bob@example.org/x" id="m2"><encrypted '
    'xmlns="eu.siacs.conversations.axolotl"><payload>AA==</payload></encrypted>'
    '<body>fallback</body></message></forwarded></result></message>',
    '2026-01-02 10:00:01,300 - RECV: <iq type="result" id="q1" to="alice@example.org/pc">'
    '<fin xmlns="urn:xmpp:mam:2" complete="true"><set xmlns="http://jabber.org/protocol/rsm">'
    '<first>A1</first><last>A2</last><count>2</count></set></fin></iq>',
    # MAM query 2: room archive, error
    '2026-01-02 10:00:02,000 - SEND: <iq id="q2" type="set" to="room@conference.example.org">'
    '<query xmlns="urn:xmpp:mam:2" queryid="q2" /></iq>',
    '2026-01-02 10:00:02,100 - RECV: <iq type="error" id="q2" from="room@conference.example.org">'
    '<error type="cancel"><item-not-found xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />'
    '</error></iq>',
    # MAM query 3: no answer
    '2026-01-02 10:00:03,000 - SEND: <iq id="q3" type="set"><query xmlns="urn:xmpp:mam:2" '
    'queryid="q3" /></iq>',
    '2026-01-02 10:00:04,000 - RECV: <presence from="carol@example.org/tab" to="alice@example.org/pc" />',
]

MAIN = [
    '2026-01-02 10:00:01 - siproxylin.app - INFO - start',
    '2026-01-02 10:00:02 - drunk-xmpp.client - ERROR - boom',
    'Traceback (most recent call last):',
    '  ValueError: x',
    '2026-01-02 10:00:04 - siproxylin.app - DEBUG - late',
]
ACC = ['2026-01-02 10:00:01 - siproxylin.account.1 - WARNING - acc line']


class TestStanzas(unittest.TestCase):
    def setUp(self):
        self.entries = siplog.parse_proto(PROTO)

    def test_parse(self):
        self.assertEqual(len(self.entries), 10)
        self.assertIsNone(self.entries[0]['el'])  # stream header
        self.assertEqual(self.entries[0]['dir'], 'SEND')
        self.assertIsNone(self.entries[1]['dir'])
        self.assertIn('line two', self.entries[3]['raw'])  # continuation line
        self.assertIsNotNone(self.entries[3]['el'])

    def test_filters(self):
        f = siplog.stanza_filter
        self.assertEqual(len(f(self.entries)), 9)
        self.assertEqual(len(f(self.entries, kind_='presence')), 1)
        self.assertEqual(len(f(self.entries, kind_='other')), 1)
        self.assertEqual(len(f(self.entries, jid='carol@example.org')), 1)
        self.assertEqual(len(f(self.entries, jid='carol@example.org/other')), 0)
        self.assertEqual(len(f(self.entries, sid='A2')), 1)  # archive id
        self.assertEqual(len(f(self.entries, sid='q1')), 4)
        self.assertEqual(len(f(self.entries, sid='o1')), 1)
        self.assertEqual(len(f(self.entries, sid='q2')), 2)
        since = siplog.parse_when('10:00:02')
        until = siplog.parse_when('2026-01-02 10:00:03', end=True)
        self.assertEqual(len(f(self.entries, since=since, until=until)), 3)
        self.assertEqual(len(f(self.entries, grep='fallback')), 1)


class TestMam(unittest.TestCase):
    def test_pairing(self):
        qs = siplog.mam_queries(siplog.parse_proto(PROTO))
        self.assertEqual(len(qs), 3)
        q1, q2, q3 = qs
        self.assertEqual(q1['with'], 'bob@example.org')
        self.assertEqual((q1['max'], q1['before']), ('25', ''))
        self.assertEqual([a for a, _ in q1['results']], ['A1', 'A2'])
        self.assertEqual((q1['first'], q1['last'], q1['count']), ('A1', 'A2', '2'))
        self.assertTrue(q1['complete'])
        self.assertIn('dur=0.300s', siplog.mam_line(q1))
        res = siplog.mam_result_lines(q1)
        self.assertIn('origin-id=o1 plain', res[0])
        self.assertIn('omemo', res[1])
        self.assertEqual(q2['to'], 'room@conference.example.org')
        self.assertEqual(q2['error'], 'item-not-found')
        self.assertIn('ERROR=item-not-found', siplog.mam_line(q2))
        self.assertIsNone(q3['end'])
        self.assertIn('NO FIN', siplog.mam_line(q3))


    def run_mam(self, *args):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'xmpp-protocol.log'
            f.write_text('\n'.join(PROTO) + '\n')
            out = io.StringIO()
            with redirect_stdout(out):
                siplog.main(['mam', str(f), *args])
            return out.getvalue().splitlines()

    def test_filter_after_pairing(self):
        # q1 sent at 10:00:01,000, fin at 10:00:01,300: fin after --until
        lines = self.run_mam('--until', '10:00:01.1')
        self.assertEqual(len(lines), 1)
        self.assertIn('results=2', lines[0])
        self.assertIn('complete=true', lines[0])
        self.assertNotIn('NO FIN', lines[0])
        # --since after q1: q1 left out, its results do not leak elsewhere
        lines = self.run_mam('--since', '10:00:01.5')
        self.assertEqual(len(lines), 2)
        self.assertTrue(all('results=0' in x for x in lines))


class TestWhen(unittest.TestCase):
    def ok(self, t, since=None, until=None):
        return siplog.in_range(datetime.strptime(t, '%Y-%m-%d %H:%M:%S,%f'),
                               siplog.parse_since(since), siplog.parse_until(until))

    def test_until_whole_second(self):
        self.assertTrue(self.ok('2026-01-02 10:00:01,500', until='10:00:01'))
        self.assertTrue(self.ok('2026-01-02 10:00:01,999', until='2026-01-02 10:00:01'))
        self.assertFalse(self.ok('2026-01-02 10:00:02,000', until='10:00:01'))
        self.assertTrue(self.ok('2026-01-02 10:00:01,000', since='10:00:01'))
        self.assertFalse(self.ok('2026-01-02 10:00:00,999', since='10:00:01'))

    def test_until_whole_unit(self):
        self.assertTrue(self.ok('2026-01-02 10:00:59,999', until='10:00'))
        self.assertFalse(self.ok('2026-01-02 10:01:00,000', until='10:00'))
        self.assertTrue(self.ok('2026-01-02 23:59:59,999', until='2026-01-02'))
        self.assertFalse(self.ok('2026-01-03 00:00:00,000', until='2026-01-02'))
        self.assertTrue(self.ok('2026-01-02 23:59:59,999', until='23:59:59'))
        self.assertFalse(self.ok('2026-01-01 23:59:59,999', since='2026-01-02'))

    def test_fraction(self):
        self.assertTrue(self.ok('2026-01-02 10:00:01,250', until='10:00:01.2'))
        self.assertFalse(self.ok('2026-01-02 10:00:01,300', until='10:00:01.2'))
        self.assertTrue(self.ok('2026-01-02 10:00:01,200', since='10:00:01,200',
                                until='10:00:01,200'))
        self.assertFalse(self.ok('2026-01-02 10:00:01,201', until='2026-01-02T10:00:01,200'))

    def test_zone_dropped(self):
        self.assertEqual(siplog.parse_since('2026-01-02T10:00:01+02:00'),
                         siplog.parse_since('2026-01-02 10:00:01'))
        self.assertEqual(siplog.parse_since('10:00:01Z'), siplog.parse_since('10:00:01'))
        self.assertEqual(siplog.parse_until('10:00:01.5-0500'),
                         siplog.parse_until('10:00:01.5'))

    def test_bad_values(self):
        for bad in ('25:00', '10:61', '2026-13-01', 'yesterday', '10', '',
                    '10:00:01.1234567', '2026-02-30 10:00'):
            with self.subTest(bad=bad):
                err = io.StringIO()
                with redirect_stderr(err), self.assertRaises(SystemExit) as cm:
                    siplog.main(['mam', '--until', bad])
                self.assertEqual(cm.exception.code, 2)
                self.assertIn('bad time', err.getvalue())
                self.assertNotIn('Traceback', err.getvalue())


class TestMerge(unittest.TestCase):
    def srcs(self):
        return [('main', MAIN), ('acc1', ACC), ('proto', PROTO)]

    def test_order(self):
        rows = siplog.merge_entries(self.srcs())
        names = [n for n, _ in rows]
        self.assertEqual(len(rows), 3 + 1 + 10)
        # 10:00:01: main, then acc1, then proto lines of that second
        i = names.index('acc1')
        self.assertEqual(names[i - 1], 'main')
        self.assertEqual(names[i + 1], 'proto')
        self.assertEqual(rows[-2][1]['text'], 'late')  # 10:00:04: main before proto
        self.assertEqual(names[-1], 'proto')
        boom = [e for _, e in rows if e.get('level') == 'ERROR'][0]
        self.assertEqual(len(boom['lines']), 3)  # traceback lines kept

    def test_filters(self):
        rows = siplog.merge_entries(self.srcs(), level='WARNING', no_proto=True)
        self.assertEqual([e['text'][:4] for _, e in rows], ['acc ', 'boom'])
        rows = siplog.merge_entries(self.srcs(), level='ERROR')
        self.assertEqual(sum(n == 'proto' for n, _ in rows), 10)
        rows = siplog.merge_entries(self.srcs(), logger='drunk', no_proto=True)
        self.assertEqual(len(rows), 1)

    def test_cli_dir_and_rotation(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'xmpp-protocol.log.1').write_text('\n'.join(PROTO[:2]) + '\n')
            (d / 'xmpp-protocol.log').write_text('\n'.join(PROTO[2:]) + '\n')
            (d / 'main.log').write_text('\n'.join(MAIN) + '\n')
            (d / 'account-1-app.log').write_text('\n'.join(ACC) + '\n')
            self.assertEqual(siplog.proto_files(d)[0].name, 'xmpp-protocol.log.1')
            out = io.StringIO()
            with redirect_stdout(out):
                siplog.main(['mam', str(d)])
            self.assertEqual(len(out.getvalue().splitlines()), 3)
            out = io.StringIO()
            with redirect_stdout(out):
                siplog.main(['merge', str(d), '--no-proto'])
            lines = out.getvalue().splitlines()
            self.assertEqual(len(lines), 6)
            self.assertTrue(lines[1].startswith('acc1 '))


if __name__ == '__main__':
    unittest.main()
