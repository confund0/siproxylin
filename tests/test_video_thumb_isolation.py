#!/usr/bin/env python3
"""
Offline tests: video thumbnails are made in a child process.

Fake worker scripts check the parent side (timeout, failure, success).
The real worker runs with a fake 'vlc' module (VLC is not in the repo venv).
"""

import json
import os
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from siproxylin.utils import video_utils  # noqa: E402

REPO_TMP = Path(__file__).resolve().parent.parent / 'tmp'


FAKE_VLC = textwrap.dedent('''
    """Fake python-vlc for the worker test. Writes a PNG and a JSON report."""
    import json, os, socket
    try:
        import resource
    except ImportError:
        resource = None

    class State:
        Playing = 3

    class _Media:
        def release(self):
            pass

    class _Player:
        def set_media(self, media):
            pass
        def play(self):
            pass
        def get_state(self):
            return State.Playing
        def get_length(self):
            return 0
        def set_position(self, pos):
            pass
        def video_take_snapshot(self, num, path, width, height):
            with open(path, 'wb') as f:
                f.write(b'\\x89PNG fake')
            report = {
                'core': resource.getrlimit(resource.RLIMIT_CORE) if resource else None,
                'cpu': resource.getrlimit(resource.RLIMIT_CPU) if resource else None,
                'as': resource.getrlimit(resource.RLIMIT_AS) if resource else None,
                'sid_is_pid': os.getsid(0) == os.getpid(),
                'net_ifaces': [n for _, n in socket.if_nameindex()],
                'env': dict(os.environ),
                'size': [width, height],
            }
            with open(os.environ['FAKE_VLC_REPORT'], 'w') as f:
                json.dump(report, f)
            return 0

    class Instance:
        def __init__(self, args):
            pass
        def media_player_new(self):
            return _Player()
        def media_new(self, path):
            return _Media()
''')


class VideoThumbIsolationTest(unittest.TestCase):

    def setUp(self):
        REPO_TMP.mkdir(exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(prefix='thumbtest-', dir=REPO_TMP))
        self.video = self.dir / 'video.mp4'
        self.video.write_bytes(b'not a real video')
        self.cache = self.dir / 'cache'
        self.out = self.cache / 'thumb.png'

    def tearDown(self):
        for p in sorted(self.dir.rglob('*'), reverse=True):
            if p.is_dir():
                p.rmdir()
            else:
                p.unlink()
        self.dir.rmdir()

    def _fake_worker(self, body):
        path = self.dir / 'fake_worker.py'
        path.write_text('import sys, os, time\n' + textwrap.dedent(body))
        return path

    def _cache_files(self):
        return sorted(p.name for p in self.cache.iterdir()) if self.cache.exists() else []

    def test_timeout_kills_child(self):
        pid_file = self.dir / 'pid'
        worker = self._fake_worker(f'''
            open({str(pid_file)!r}, 'w').write(str(os.getpid()))
            time.sleep(60)
        ''')
        with mock.patch.object(video_utils, 'WORKER_PATH', worker), \
                mock.patch.object(video_utils, 'WORKER_TIMEOUT', 1):
            start = time.monotonic()
            result = video_utils.generate_video_thumbnail(self.video, self.out)
            elapsed = time.monotonic() - start
        self.assertIsNone(result)
        self.assertLess(elapsed, 10)
        pid = int(pid_file.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertEqual(self._cache_files(), [])

    def test_exit_1_gives_none_and_no_file(self):
        worker = self._fake_worker('''
            open(sys.argv[2], 'wb').write(b'partial')
            sys.stderr.write('boom\\n')
            sys.exit(1)
        ''')
        with mock.patch.object(video_utils, 'WORKER_PATH', worker):
            result = video_utils.generate_video_thumbnail(self.video, self.out)
        self.assertIsNone(result)
        self.assertEqual(self._cache_files(), [])

    def test_crash_gives_none(self):
        worker = self._fake_worker('''
            import signal
            os.kill(os.getpid(), signal.SIGSEGV)
        ''')
        with mock.patch.object(video_utils, 'WORKER_PATH', worker):
            result = video_utils.generate_video_thumbnail(self.video, self.out)
        self.assertIsNone(result)
        self.assertEqual(self._cache_files(), [])

    def test_exit_0_with_empty_file_gives_none(self):
        worker = self._fake_worker('''
            sys.exit(0)
        ''')
        with mock.patch.object(video_utils, 'WORKER_PATH', worker):
            result = video_utils.generate_video_thumbnail(self.video, self.out)
        self.assertIsNone(result)
        self.assertEqual(self._cache_files(), [])

    def test_success_renames_to_final_path(self):
        worker = self._fake_worker('''
            open(sys.argv[2], 'wb').write(b'png data')
        ''')
        with mock.patch.object(video_utils, 'WORKER_PATH', worker):
            result = video_utils.generate_video_thumbnail(self.video, self.out)
        self.assertEqual(result, str(self.out))
        self.assertEqual(self.out.read_bytes(), b'png data')
        self.assertEqual(self._cache_files(), ['thumb.png'])

    def test_get_or_generate_uses_worker(self):
        worker = self._fake_worker('''
            open(sys.argv[2], 'wb').write(b'png data')
        ''')
        with mock.patch.object(video_utils, 'WORKER_PATH', worker):
            result = video_utils.get_or_generate_thumbnail(self.video, self.cache)
        self.assertIsNotNone(result)
        self.assertTrue(Path(result).is_file())
        self.assertEqual(self._cache_files(), [Path(result).name])

    def test_env_filter(self):
        env = {
            'DISPLAY': ':0',
            'WAYLAND_DISPLAY': 'wayland-0',
            'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/1/bus',
            'XDG_RUNTIME_DIR': '/run/user/1',
            'PULSE_SERVER': 'unix:/x',
            'PATH': '/usr/bin',
            'PYTHONPATH': '/app/lib',
            'VLC_PLUGIN_PATH': '/app/vlc',
            'LD_LIBRARY_PATH': '/app/lib',
            'HOME': '/home/x',
        }
        with mock.patch.dict(os.environ, env, clear=True):
            child_env = video_utils._worker_env()
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS',
                     'XDG_RUNTIME_DIR', 'PULSE_SERVER'):
            self.assertNotIn(name, child_env)
        for name in ('PATH', 'PYTHONPATH', 'VLC_PLUGIN_PATH', 'LD_LIBRARY_PATH', 'HOME'):
            self.assertEqual(child_env[name], env[name])

    def _fake_vlc_env(self):
        vlc_dir = self.dir / 'fakevlc'
        vlc_dir.mkdir()
        (vlc_dir / 'vlc.py').write_text(FAKE_VLC)
        self.report = self.dir / 'report.json'
        return {
            'PYTHONPATH': str(vlc_dir),
            'FAKE_VLC_REPORT': str(self.report),
        }

    def _wrapped_worker(self, prelude):
        """Script that runs `prelude`, then the real worker as __main__."""
        path = self.dir / 'wrapped_worker.py'
        path.write_text(textwrap.dedent(prelude) + textwrap.dedent(f'''
            import runpy, sys
            sys.argv[0] = {str(video_utils.WORKER_PATH)!r}
            runpy.run_path(sys.argv[0], run_name='__main__')
        '''))
        return path

    def test_real_worker_with_fake_vlc(self):
        """Real worker script: limits, new session, env filter, output file."""
        extra = self._fake_vlc_env()
        extra['DISPLAY'] = ':0'
        extra['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=/run/user/1/bus'
        report = self.report
        with mock.patch.dict(os.environ, extra):
            result = video_utils.generate_video_thumbnail(self.video, self.out, width=320, height=0)
        self.assertEqual(result, str(self.out))
        self.assertEqual(self.out.read_bytes(), b'\x89PNG fake')
        self.assertEqual(self._cache_files(), ['thumb.png'])

        data = json.loads(report.read_text())
        self.assertEqual(data['core'], [0, 0])
        self.assertEqual(data['cpu'][0], 20)
        self.assertEqual(data['as'][0], 2 * 1024 ** 3)
        self.assertTrue(data['sid_is_pid'])
        self.assertNotIn('DISPLAY', data['env'])
        self.assertNotIn('DBUS_SESSION_BUS_ADDRESS', data['env'])
        self.assertEqual(data['size'], [320, 0])
        # Network namespace is best effort; print the result for the report
        print(f"\nworker network interfaces: {data['net_ifaces']}", file=sys.stderr)

    def test_worker_without_resource_module(self):
        """No resource module (Windows): the worker still makes the snapshot."""
        worker = self._wrapped_worker('''
            import sys
            sys.modules['resource'] = None  # import resource now raises ImportError
        ''')
        with mock.patch.dict(os.environ, self._fake_vlc_env()), \
                mock.patch.object(video_utils, 'WORKER_PATH', worker), \
                self.assertLogs(video_utils.logger, level='DEBUG') as logs:
            result = video_utils.generate_video_thumbnail(self.video, self.out)
        self.assertEqual(result, str(self.out))
        self.assertEqual(self.out.read_bytes(), b'\x89PNG fake')
        self.assertEqual(self._cache_files(), ['thumb.png'])
        data = json.loads(self.report.read_text())
        self.assertIsNone(data['core'])
        self.assertTrue(any('no resource module' in line for line in logs.output))

    def test_worker_with_failing_setrlimit(self):
        """RLIMIT_AS fails (as on macOS): other limits set, snapshot made."""
        worker = self._wrapped_worker('''
            import resource
            _setrlimit = resource.setrlimit
            def _fake_setrlimit(which, value):
                if which == resource.RLIMIT_AS:
                    raise ValueError('not allowed')
                return _setrlimit(which, value)
            resource.setrlimit = _fake_setrlimit
        ''')
        with mock.patch.dict(os.environ, self._fake_vlc_env()), \
                mock.patch.object(video_utils, 'WORKER_PATH', worker), \
                self.assertLogs(video_utils.logger, level='DEBUG') as logs:
            result = video_utils.generate_video_thumbnail(self.video, self.out)
        self.assertEqual(result, str(self.out))
        self.assertEqual(self._cache_files(), ['thumb.png'])
        data = json.loads(self.report.read_text())
        self.assertEqual(data['core'], [0, 0])
        self.assertEqual(data['cpu'][0], 20)
        self.assertNotEqual(data['as'][0], 2 * 1024 ** 3)
        self.assertTrue(any('RLIMIT_AS not set: not allowed' in line for line in logs.output))


if __name__ == '__main__':
    unittest.main()
