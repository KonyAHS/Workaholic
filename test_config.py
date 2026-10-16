import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from service_aggregator import App, Settings, config_path


class ConfigChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.legacy = root / "app"
        self.legacy.mkdir()
        self.config = root / "roaming/Workaholic/services_config.json"
        self.app = SimpleNamespace(opts=SimpleNamespace(smoke_test=False), services=[],
                                   groups=[], settings=Settings(), _config_warning=None)
        for name, value in (("APP_DIR", self.legacy), ("CONFIG_FILE", self.config)):
            p = patch("service_aggregator." + name, value)
            p.start()
            self.addCleanup(p.stop)

    def write_legacy(self):
        path = self.legacy / "services_config.json"
        path.write_text(json.dumps({"services": [{"path": "helper.py", "name": "My helper"}]}))
        return path

    def test_settings_path_uses_roaming_appdata(self):
        if os.name != "nt":
            self.skipTest("Windows path")
        with patch.dict(os.environ, {"APPDATA": str(self.legacy)}):
            self.assertEqual(config_path(), self.legacy / "Workaholic/services_config.json")

    def test_legacy_migration_preserves_original_and_services(self):
        legacy = self.write_legacy()
        original = legacy.read_bytes()
        App._load_config(self.app)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(legacy.read_bytes(), original)
        self.assertEqual(self.app.services[0].name, "My helper")

    def test_existing_appdata_takes_priority(self):
        self.write_legacy()
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{"services": []}')
        App._load_config(self.app)
        self.assertEqual(self.app.services, [])
        self.assertEqual(self.config.read_text(), '{"services": []}')

    def test_failed_copy_still_loads_services(self):
        self.write_legacy()
        with patch("service_aggregator.shutil.copyfile", side_effect=PermissionError("read only")):
            App._load_config(self.app)
        self.assertEqual(self.app.services[0].name, "My helper")
        self.assertIn("Couldn't migrate", self.app._config_warning)

    def test_save_creates_appdata_folder_and_leaves_exe_folder_alone(self):
        App._save_config(self.app)
        self.assertEqual(json.loads(self.config.read_text())["services"], [])
        self.assertEqual(list(self.legacy.iterdir()), [])

    def test_smoke_test_never_migrates(self):
        self.write_legacy()
        self.app.opts.smoke_test = True
        App._load_config(self.app)
        self.assertFalse(self.config.exists())
        self.assertEqual(self.app.services, [])
