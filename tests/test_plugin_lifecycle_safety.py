"""Regression matrix for ownership and transactional plugin uninstallation."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from core.plugin_runtime import PluginRuntime, _safe_native_dir_name


class _IsolatedPluginFixture(TestCase):
    """Prepare an isolated fake project instead of modifying the real project."""

    def setUp(self):
        self.temp = TemporaryDirectory(prefix="aas-lifecycle-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = PluginRuntime(self.root)
        self.spec = "fault_types"
        self.name = "plugin_one"
        self.config_dir = self.root / "configs" / "fault_types"
        self.code_dir = self.root / "src" / "fault_plugins"
        self.config_dir.mkdir(parents=True)
        self.code_dir.mkdir(parents=True)
        self.descriptor_path = self.config_dir / "plugin_one.yaml"
        self.code_path = self.code_dir / "plugin_one.py"
        self.descriptor_path.write_text(
            "name: plugin_one\nclass_path: src.fault_plugins.plugin_one.TestPlugin\n", encoding="utf-8"
        )
        self.code_path.write_text("# owned implementation\n", encoding="utf-8")
        self.manifest_entry = {
            "type": self.spec, "name": self.name,
            "yaml": self.descriptor_path.relative_to(self.root).as_posix(),
            "code": self.code_path.relative_to(self.root).as_posix(),
            "library": "", "source": "", "language": "python",
        }
        self._write_manifest({f"{self.spec}:{self.name}": self.manifest_entry})
        self.registry = {self.name: object()}
        self.fake_module = SimpleNamespace(
            FAULT_TYPE_REGISTRY=self.registry,
            discover_fault_types=lambda _: 1,
        )

    def _write_manifest(self, plugins):
        path = self.root / "configs" / ".aas_plugin_installations.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "plugins": plugins}), encoding="utf-8")

    def _uninstall(self, *, force):
        with patch("core.plugin_runtime.importlib.import_module", return_value=self.fake_module):
            return self.runtime.uninstall(self.spec, self.name, force=force)



class PluginLifecycleSafetyTests(_IsolatedPluginFixture):
    """Prove unsafe uninstall manifests cannot delete unrelated files."""

    def test_preview_is_read_only_and_valid_uninstall_removes_owned_files(self):
        original = self.runtime._manifest_path.read_bytes()  # pylint: disable=protected-access
        preview = self._uninstall(force=False)
        self.assertTrue(preview["code_will_remove"])
        self.assertEqual(self.runtime._manifest_path.read_bytes(), original)  # pylint: disable=protected-access
        self.assertTrue(self.code_path.exists())
        result = self._uninstall(force=True)
        self.assertTrue(result["uninstalled"])
        self.assertFalse(self.code_path.exists())
        self.assertFalse(self.descriptor_path.exists())
        self.assertNotIn(self.name, self.registry)

    def test_manifest_cannot_delete_unrelated_root_file(self):
        other = self.root / "README.md"
        other.write_text("KEEP", encoding="utf-8")
        self.manifest_entry["code"] = "README.md"
        self._write_manifest({f"{self.spec}:{self.name}": self.manifest_entry})
        preview = self._uninstall(force=False)
        self.assertFalse(preview["code_will_remove"])
        self.assertTrue(preview["ownership_warnings"])
        self._uninstall(force=True)
        self.assertEqual(other.read_text(encoding="utf-8"), "KEEP")
        self.assertTrue(self.code_path.exists())

    def test_manifest_cannot_delete_another_plugin_implementation(self):
        sibling = self.code_dir / "plugin_two.py"
        sibling.write_text("KEEP", encoding="utf-8")
        self.manifest_entry["code"] = "src/fault_plugins/plugin_two.py"
        self._write_manifest({f"{self.spec}:{self.name}": self.manifest_entry})
        self._uninstall(force=True)
        self.assertEqual(sibling.read_text(encoding="utf-8"), "KEEP")

    def test_manifest_mismatched_descriptor_does_not_confer_ownership(self):
        self.manifest_entry["yaml"] = "configs/fault_types/other.yaml"
        self._write_manifest({f"{self.spec}:{self.name}": self.manifest_entry})
        self._uninstall(force=True)
        self.assertTrue(self.code_path.exists())

    def test_shared_manifest_ownership_retains_code(self):
        self._write_manifest({
            f"{self.spec}:{self.name}": self.manifest_entry,
            "fault_types:another": {
                "type": "fault_types", "name": "another",
                "code": "src/fault_plugins/plugin_one.py",
            },
        })
        preview = self._uninstall(force=False)
        self.assertFalse(preview["code_will_remove"])
        self._uninstall(force=True)
        self.assertTrue(self.code_path.exists())

    def test_shared_descriptor_retain_implementation(self):
        (self.config_dir / "plugin_two.yaml").write_text(
            "name: plugin_two\nclass_path: src.fault_plugins.plugin_one.TestPlugin\n", encoding="utf-8"
        )
        self._uninstall(force=True)
        self.assertTrue(self.code_path.exists())

    def test_symlink_implementation_is_retained(self):
        target = self.root / "protected.txt"
        target.write_text("KEEP", encoding="utf-8")
        self.code_path.unlink()
        try:
            self.code_path.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are not supported by this host")
        self.assertFalse(self._uninstall(force=False)["code_will_remove"])
        self._uninstall(force=True)
        self.assertTrue(self.code_path.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), "KEEP")

    def test_descriptor_symlink_rejected(self):
        actual = self.descriptor_path.with_suffix(".backup")
        self.descriptor_path.rename(actual)
        try:
            self.descriptor_path.symlink_to(actual)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are not supported by this host")
        with self.assertRaisesRegex(ValueError, "插件描述文件"):
            self._uninstall(force=True)
        self.assertTrue(actual.exists())

    def test_native_artifact_restricted_to_own_plugin_directory(self):
        native_dir = self.root / "native_plugins" / self.spec / _safe_native_dir_name(self.name)
        native_dir.mkdir(parents=True)
        library = native_dir / "own.so"
        library.write_bytes(b"OWN")
        other = self.root / "native_plugins" / self.spec / _safe_native_dir_name("another") / "other.so"
        other.parent.mkdir(parents=True)
        other.write_bytes(b"KEEP")
        library_rel = library.relative_to(self.root).as_posix()
        other_rel = other.relative_to(self.root).as_posix()
        self.descriptor_path.write_text(
            f"name: {self.name}\nlanguage: c\nlibrary: {library_rel}\n", encoding="utf-8"
        )
        self.manifest_entry.update({"language": "c", "library": other_rel})
        self._write_manifest({f"{self.spec}:{self.name}": self.manifest_entry})
        self._uninstall(force=True)
        self.assertTrue(other.exists())
        self.assertTrue(library.exists())

    def test_native_artifact_only_removed_with_descriptor_match(self):
        native_dir = self.root / "native_plugins" / self.spec / _safe_native_dir_name(self.name)
        native_dir.mkdir(parents=True)
        library = native_dir / "own.so"
        library.write_bytes(b"OWN")
        rel = library.relative_to(self.root).as_posix()
        self.descriptor_path.write_text(
            f"name: {self.name}\nlanguage: c\nlibrary: {rel}\n", encoding="utf-8"
        )
        self.manifest_entry.update({"language": "c", "library": rel})
        self._write_manifest({f"{self.spec}:{self.name}": self.manifest_entry})
        self.assertTrue(self._uninstall(force=False)["library_will_remove"])
        self._uninstall(force=True)
        self.assertFalse(library.exists())

    def test_native_file_rollback_restores_both_library_and_source(self):
        native_dir = self.root / "native_plugins" / self.spec / _safe_native_dir_name(self.name)
        native_dir.mkdir(parents=True)
        library = native_dir / "own.so"
        source = native_dir / "own.c"
        library.write_bytes(b"LIBRARY")
        source.write_bytes(b"SOURCE")
        library_rel = library.relative_to(self.root).as_posix()
        source_rel = source.relative_to(self.root).as_posix()
        self.descriptor_path.write_text(
            f"name: {self.name}\nlanguage: c\nlibrary: {library_rel}\nsource: {source_rel}\n",
            encoding="utf-8",
        )
        self.manifest_entry.update({"language": "c", "library": library_rel, "source": source_rel})
        self._write_manifest({f"{self.spec}:{self.name}": self.manifest_entry})
        before_manifest = self.runtime._manifest_path.read_bytes()  # pylint: disable=protected-access
        with patch("core.plugin_runtime.importlib.import_module", return_value=self.fake_module):
            with patch.object(self.runtime, "_save_manifest", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    self.runtime.uninstall(self.spec, self.name, force=True)
        self.assertEqual(library.read_bytes(), b"LIBRARY")
        self.assertEqual(source.read_bytes(), b"SOURCE")
        self.assertTrue(self.descriptor_path.exists())
        self.assertEqual(self.runtime._manifest_path.read_bytes(), before_manifest)  # pylint: disable=protected-access

    def test_failed_manifest_save_rolls_back_files_and_registry(self):
        original_manifest = self.runtime._manifest_path.read_bytes()  # pylint: disable=protected-access
        with patch("core.plugin_runtime.importlib.import_module", return_value=self.fake_module):
            with patch.object(self.runtime, "_save_manifest", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    self.runtime.uninstall(self.spec, self.name, force=True)
        self.assertTrue(self.code_path.exists())
        self.assertTrue(self.descriptor_path.exists())
        self.assertEqual(self.runtime._manifest_path.read_bytes(), original_manifest)  # pylint: disable=protected-access
        self.assertIn(self.name, self.registry)

    def test_builtin_protection(self):
        with self.assertRaisesRegex(ValueError, "内置插件"):
            self.runtime.uninstall(self.spec, "spike", force=True)

class PluginInstallationProtectionTests(_IsolatedPluginFixture):
    """Import must not overwrite artifacts outside the same installation."""

    def _install(self, *, yaml_name="plugin_one.yaml", code_name="plugin_one.py", name="plugin_one", discover=None):
        descriptor = f"name: {name}\nclass_path: src.fault_plugins.plugin_one.TestPlugin\n"
        if discover is None:
            def discover(_):
                self.registry[name] = SimpleNamespace(name=name, inject=lambda _data, _config: None)
                return 1
        fake = SimpleNamespace(FAULT_TYPE_REGISTRY=self.registry, discover_fault_types=discover)
        with patch("core.plugin_runtime.importlib.import_module", return_value=fake):
            return self.runtime.install(
                "fault_types", yaml_name, descriptor, code_name,
                "class TestPlugin:\n    pass\n",
            )

    def test_reinstall_same_owned_plugin_is_permitted(self):
        result = self._install()
        self.assertTrue(result["registered"], result)
        self.assertIn("class TestPlugin", self.code_path.read_text(encoding="utf-8"))

    def test_unmanaged_implementation_cannot_be_overwritten(self):
        self._write_manifest({})
        original = self.code_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "不属于该插件"):
            self._install()
        self.assertEqual(self.code_path.read_bytes(), original)

    def test_built_in_descriptor_filename_cannot_be_claimed(self):
        self.descriptor_path.unlink()
        with self.assertRaisesRegex(ValueError, "内置插件描述"):
            self._install(yaml_name="spike.yaml")

    def test_cross_plugin_yaml_filename_cannot_be_reused(self):
        self.manifest_entry["name"] = "another"
        self._write_manifest({"fault_types:another": self.manifest_entry})
        original = self.descriptor_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "不属于该插件"):
            self._install()
        self.assertEqual(self.descriptor_path.read_bytes(), original)

    def test_failure_during_discovery_rolls_back_original_files(self):
        original = self.code_path.read_bytes()
        def fail_discover(_):
            raise RuntimeError("discovery failure")
        result = self._install(discover=fail_discover)
        self.assertFalse(result["registered"])
        self.assertTrue(result["rolled_back"])
        self.assertEqual(self.code_path.read_bytes(), original)
        self.assertEqual(self.descriptor_path.read_text(encoding="utf-8"),
                         "name: plugin_one\nclass_path: src.fault_plugins.plugin_one.TestPlugin\n")

    def test_backslash_filename_rejected_consistently_across_platforms(self):
        with self.assertRaisesRegex(ValueError, "不能包含目录"):
            self._install(code_name=r"..\\other.py")

    def test_symlinked_implementation_parent_is_rejected(self):
        protected = self.root / "external"
        protected.mkdir()
        self.code_path.unlink()
        self.code_dir.rmdir()
        try:
            self.code_dir.symlink_to(protected, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "符号链接"):
            self._install()
        self.assertFalse((protected / "plugin_one.py").exists())

    def test_builtin_plugin_name_cannot_be_overridden_using_custom_yaml(self):
        with self.assertRaisesRegex(ValueError, "内置插件名称"):
            self._install(name="spike")

    def test_duplicate_plugin_name_in_different_yaml_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "同名插件"):
            self._install(yaml_name="new_descriptor.yaml")
