"""Tests for the top-bar menu's two versions and how Dimly picks between them."""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_home = tempfile.mkdtemp(prefix="dimly-test-")
for name in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "XDG_DATA_HOME"):
    os.environ[name] = os.path.join(_home, name.lower())
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import ddc  # noqa: E402
import integration  # noqa: E402

EXTENSION = ROOT / "extension"


class VersionsTest(unittest.TestCase):
    def test_gnome45_is_generated_from_gnome42(self):
        spec = importlib.util.spec_from_file_location("make_gnome45", EXTENSION / "make-gnome45.py")
        maker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(maker)
        self.assertEqual(maker.generate(), (EXTENSION / "gnome45" / "extension.js").read_text(),
                         "gnome45/extension.js is out of date: run extension/make-gnome45.py")

    def test_together_they_cover_gnome_42_to_50_once_each(self):
        versions = []
        for variant in ("gnome42", "gnome45"):
            meta = json.loads((EXTENSION / variant / "metadata.json").read_text())
            self.assertEqual(meta["uuid"], integration.EXTENSION_UUID)
            versions += [int(v) for v in meta["shell-version"]]
        self.assertEqual(sorted(versions), list(range(42, 51)))


class PickingTest(unittest.TestCase):
    def setUp(self):
        self.patches = [mock.patch.object(integration, "EXTENSIONS_DIR", str(EXTENSION))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_picks_the_version_gnome_needs(self):
        for version, variant in ((42, "gnome42"), (44, "gnome42"), (45, "gnome45"), (50, "gnome45")):
            with self.subTest(version=version), mock.patch.object(integration, "gnome_version", return_value=version):
                self.assertEqual(integration.bundled_extension(), str(EXTENSION / variant))
        for version in (None, 40):
            with mock.patch.object(integration, "gnome_version", return_value=version):
                self.assertIsNone(integration.bundled_extension())

    def test_reads_gnome_shells_version_text(self):
        with mock.patch.object(integration.Gio, "bus_get_sync", side_effect=integration.GLib.Error()), \
                mock.patch.object(subprocess, "run", return_value=SimpleNamespace(stdout="GNOME Shell 50.1\n")):
            self.assertEqual(integration.gnome_version(), 50)

    def test_linking_replaces_an_old_copy_and_follows_upgrades(self):
        link = Path(_home) / "extensions" / integration.EXTENSION_UUID
        link.mkdir(parents=True)
        (link / "extension.js").write_text("// copied by an older install.sh")
        with mock.patch.object(integration, "USER_EXTENSION", str(link)), \
                mock.patch.object(integration, "_installed_from_source", return_value=True):
            with mock.patch.object(integration, "gnome_version", return_value=42):
                self.assertTrue(integration.link_extension())
                self.assertEqual(os.path.realpath(link), str(EXTENSION / "gnome42"))
                self.assertFalse(integration.link_extension())  # already right
            with mock.patch.object(integration, "gnome_version", return_value=50):
                self.assertTrue(integration.link_extension())  # e.g. after upgrading Ubuntu
                self.assertEqual(os.path.realpath(link), str(EXTENSION / "gnome45"))
            integration.unlink_extension()
            self.assertFalse(link.exists() or link.is_symlink())


    def test_a_package_install_leaves_your_account_alone(self):
        link = Path(_home) / "untouched" / integration.EXTENSION_UUID
        with mock.patch.object(integration, "USER_EXTENSION", str(link)), \
                mock.patch.object(integration, "_installed_from_source", return_value=False), \
                mock.patch.object(integration, "gnome_version", return_value=50):
            self.assertFalse(integration.link_extension())
        self.assertFalse(link.exists())


class CinnamonPanelTest(unittest.TestCase):
    others = ["panel1:left:0:menu@cinnamon.org:1", "panel2:right:2:calendar@cinnamon.org:13"]
    uuid = integration.EXTENSION_UUID

    def test_a_new_applet_takes_cinnamons_next_free_id(self):
        entries, next_id = integration.applet_entries(self.others, True, "right", "panel1", 20)
        self.assertEqual(entries, self.others + [f"panel1:right:0:{self.uuid}:20"])
        self.assertEqual(next_id, 21)
        entries, next_id = integration.applet_entries(self.others, True, "right", "panel1", 5)
        self.assertEqual(entries[-1], f"panel1:right:0:{self.uuid}:14")  # never reuse a taken ID
        self.assertEqual(next_id, 15)

    def test_moving_keeps_its_panel_and_id(self):
        placed = self.others + [f"panel2:right:3:{self.uuid}:7"]
        moved, next_id = integration.applet_entries(placed, True, "left", "panel1", 30)
        self.assertEqual(moved, self.others + [f"panel2:left:0:{self.uuid}:7"])
        self.assertEqual(next_id, 30)
        same_zone, _ = integration.applet_entries(placed, True, "right", "panel1", 30)
        self.assertEqual(same_zone[-1], f"panel2:right:3:{self.uuid}:7")  # order kept too

    def test_removing(self):
        placed = self.others + [f"panel1:center:0:{self.uuid}:7"]
        self.assertEqual(integration.applet_entries(placed, False, "center", "panel1", 30), (self.others, 30))

    def test_applet_metadata_matches(self):
        meta = json.loads((EXTENSION / "cinnamon" / "metadata.json").read_text())
        self.assertEqual(meta["uuid"], self.uuid)


class InterfaceTest(unittest.TestCase):
    def test_menus_ask_the_service_only_for_what_it_offers(self):
        import xml.etree.ElementTree as ElementTree

        import service

        def members(xml):
            return {(member.tag, member.get("name"), tuple((arg.get("type"), arg.get("direction"))
                                                           for arg in member.findall("arg")))
                    for member in ElementTree.fromstring(xml).find("interface")}

        offered = members(service.INTERFACE_XML)
        for menu in ("gnome42/extension.js", "gnome45/extension.js", "cinnamon/applet.js"):
            xml = re.search(r"<node>.*?</node>", (EXTENSION / menu).read_text(), re.S).group(0)
            with self.subTest(menu=menu):
                self.assertLessEqual(members(xml), offered)


class PositionTest(unittest.TestCase):
    def test_unknown_positions_fall_back_to_right(self):
        ddc.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ddc.CONFIG_FILE.write_text('{"top_bar_position": "bottom"}')
        self.assertEqual(ddc.load_config()["top_bar_position"], "right")
        ddc.update_config(top_bar_position="left")
        self.assertEqual(ddc.load_config()["top_bar_position"], "left")


if __name__ == "__main__":
    unittest.main()
