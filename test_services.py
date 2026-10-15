import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

from service_aggregator import App, Group, Service, matching_process, process_snapshot, trigger_process_running


class GroupChecks(unittest.TestCase):
    def test_trigger_matches_name_or_exact_path(self):
        path = str(Path.cwd() / "game" / "ffxiv_dx11.exe")
        processes = [{"name": "FFXIV_DX11.EXE", "exe": path}]
        self.assertTrue(trigger_process_running("ffxiv_dx11.exe", processes))
        self.assertTrue(trigger_process_running(path, processes))
        self.assertFalse(trigger_process_running(str(Path.cwd() / "other" / "ffxiv_dx11.exe"), processes))
        self.assertFalse(trigger_process_running("ffxiv.exe", processes))
        self.assertFalse(trigger_process_running("", processes))
        self.assertTrue(trigger_process_running("ffxiv_dx11.exe", [{"name": "ffxiv_dx11.exe", "exe": None}]))
        self.assertFalse(trigger_process_running(path, [{"name": "ffxiv_dx11.exe", "exe": None}]))

    def test_triggers_once_per_session_and_deduplicates_shared_services(self):
        service = Service("helper.py")
        groups = [Group("Game", trigger_process="game.exe", start_delay=2),
                  Group("Tools", trigger_process="game.exe")]
        app = SimpleNamespace(_closing=False, opts=SimpleNamespace(no_autostart=False), groups=groups,
                              _trigger_states={}, group_services=lambda g: [service], _start_sequence=Mock())
        running = [{"name": "game.exe"}]
        App._check_process_triggers(app, running)
        self.assertEqual(app._start_sequence.call_args.args[0], [(service, 2)])
        App._check_process_triggers(app, running)
        self.assertEqual(app._start_sequence.call_count, 1)
        App._check_process_triggers(app, [])
        App._check_process_triggers(app, running)
        self.assertEqual(app._start_sequence.call_count, 2)
        app.opts.no_autostart = True
        App._check_process_triggers(app, [])
        App._check_process_triggers(app, running)
        self.assertEqual(app._start_sequence.call_count, 2)

    def restart_app(self):
        first = Mock(external=None, is_active=True)
        second = Mock(external=None, is_active=False)
        external = Mock(external={"pid": 123}, is_active=True)
        group = Group("Game", start_delay=2)
        members = [first, second, external]
        app = SimpleNamespace(_closing=False, _destroyed=False, _group_restart_tokens={},
                              services=members, group_services=lambda g: members, flash=Mock(),
                              _refresh=Mock(), after=Mock(), _start_sequence=Mock())
        return app, group, first, second, external

    def test_group_restart_waits_for_stop_and_preserves_order(self):
        app, group, first, second, external = self.restart_app()
        App.restart_group(app, group)
        first.stop.assert_called_once_with()
        external.stop.assert_not_called()
        app._start_sequence.assert_not_called()
        first.is_active = False
        app.after.call_args.args[1]()
        self.assertEqual(app._start_sequence.call_args.args[0], [(first, 2), (second, 2)])

    def test_stop_group_cancels_pending_restart(self):
        app, group, first, second, external = self.restart_app()
        App.restart_group(app, group)
        resume = app.after.call_args.args[1]
        App.stop_group(app, group)
        first.is_active = False
        resume()
        app._start_sequence.assert_not_called()


class ProcessChecks(unittest.TestCase):
    def test_external_script_is_not_duplicated_or_stopped(self):
        with tempfile.TemporaryDirectory(prefix="Workaholic test ") as folder:
            script = Path(folder) / "worker.py"
            script.write_text("import time\ntime.sleep(60)\n")
            proc = subprocess.Popen([sys.executable, str(script)], cwd=folder)
            try:
                service = Service(str(script))
                self.assertFalse(service.start())
                self.assertEqual(service.pid, proc.pid)
                self.assertEqual(service.status()[1], "Running elsewhere")
                self.assertIsNone(service.proc)
                service.stop(restart=True)
                self.assertIsNone(proc.poll())
                self.assertFalse(service.restart_pending)
                proc.terminate()
                proc.wait(5)
                self.assertTrue(service.start())
                self.assertIsNotNone(service.proc)
                pid = service.proc.pid
                service.start()
                self.assertEqual(service.proc.pid, pid)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(5)
                if 'service' in locals() and service.proc:
                    service._stop_worker(service.proc)

    def test_other_python_script_and_argument_are_not_matches(self):
        target = str(Path.cwd() / "target.py")
        info = {"pid": 123, "exe": sys.executable, "cmdline": [sys.executable, "other.py", target],
                "cwd": str(Path.cwd())}
        self.assertIsNone(matching_process(target, [info]))
        info["cmdline"] = [sys.executable, "-c", "print('hello')", target]
        self.assertIsNone(matching_process(target, [info]))
        info["cmdline"] = [sys.executable, "-u", "target.py"]
        self.assertEqual(matching_process(target, [info]), info)

    def test_executable_uses_full_path_and_handles_restricted_metadata(self):
        target = str(Path.cwd() / "one" / "app.exe")
        self.assertIsNone(matching_process(target, [{"exe": None, "cmdline": None}]))
        self.assertIsNone(matching_process(target, [{"exe": str(Path.cwd() / "two" / "app.exe")}]))
        info = {"exe": target, "pid": 123}
        self.assertEqual(matching_process(target, [info]), info)

    def test_frozen_python_does_not_launch_workaholic_again(self):
        from service_aggregator import default_python
        with patch.object(sys, "frozen", True, create=True), patch("shutil.which", return_value="C:/Python/python.exe"):
            self.assertEqual(default_python(), "C:/Python/python.exe")


if __name__ == "__main__":
    unittest.main()
