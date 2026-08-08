import sys
import tempfile
import unittest
from pathlib import Path

from action0.service import DefinitionError
from action0.service import DuplicateServiceError
from action0.service import Registry
from action0.service import ServiceError

# Entry points belong to installed distributions, so the tests fake one: a
# plugin module plus a .dist-info directory (METADATA + entry_points.txt)
# are generated into a temp dir on sys.path at import time (cleaned up by
# TemporaryDirectory at exit), mirroring the loader-test pattern.
MODULE = "a0svc_plugin_mod"
DISTRIBUTION = "a0svc-demo-plugin"

_MODULE_SOURCE = '''
"""Generated plugin services for the entry-point tests."""


class Widget:
    def __init__(self, size=1):
        self.size = size


class Gadget:
    pass


def make_widget() -> Widget:
    return Widget(99)


def setup_services(registry):
    registry.register(Gadget, name="hooked-gadget")
    registry.register_instance("marker", name="hooked-marker")


def failing_hook(registry):
    raise RuntimeError("boom")


NOT_CALLABLE = 42
'''

_ENTRY_POINTS = f"""
[a0svc-test.classes]
widget = {MODULE}:Widget

[a0svc-test.factories]
made-widget = {MODULE}:make_widget

[a0svc-test.hooks]
setup = {MODULE}:setup_services

[a0svc-test.broken-load]
missing = {MODULE}:does_not_exist

[a0svc-test.broken-object]
constant = {MODULE}:NOT_CALLABLE

[a0svc-test.broken-hook]
failing = {MODULE}:failing_hook
"""

_METADATA = f"""Metadata-Version: 2.1
Name: {DISTRIBUTION}
Version: 1.0
"""

_TMP = tempfile.TemporaryDirectory()
Path(_TMP.name, f"{MODULE}.py").write_text(_MODULE_SOURCE, encoding="utf-8")
_DIST_INFO = Path(_TMP.name, "a0svc_demo_plugin-1.0.dist-info")
_DIST_INFO.mkdir()
(_DIST_INFO / "METADATA").write_text(_METADATA, encoding="utf-8")
(_DIST_INFO / "entry_points.txt").write_text(_ENTRY_POINTS, encoding="utf-8")
sys.path.insert(0, _TMP.name)


class LoadEntryPointsTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.service.registry.Registry.load_entry_points`
    """

    def test_class_entry_registers_under_the_entry_point_name(self) -> None:
        """
        A class entry point becomes a named service registration.
        """
        registry = Registry()
        definitions = registry.load_entry_points("a0svc-test.classes")
        self.assertEqual([definition.name for definition in definitions], ["widget"])
        self.assertEqual(registry.get("widget").size, 1)

    def test_factory_entry_uses_the_return_annotation(self) -> None:
        """
        A factory entry point registers like any factory callable.
        """
        registry = Registry()
        registry.load_entry_points("a0svc-test.factories")
        self.assertEqual(registry.get("made-widget").size, 99)

    def test_setup_hook_is_called_with_the_registry(self) -> None:
        """
        A one-required-parameter function is called as a setup hook, and
        the definitions it adds are collected.
        """
        registry = Registry()
        definitions = registry.load_entry_points("a0svc-test.hooks")
        self.assertEqual(
            sorted(definition.name or "" for definition in definitions),
            ["hooked-gadget", "hooked-marker"],
        )
        self.assertEqual(registry.get("hooked-marker"), "marker")
        self.assertIn("hooked-gadget", registry)

    def test_load_failure_names_entry_point_and_distribution(self) -> None:
        """
        An unresolvable entry point raises with full context.
        """
        registry = Registry()
        with self.assertRaises(DefinitionError) as caught:
            registry.load_entry_points("a0svc-test.broken-load")
        self.assertIn("'missing'", str(caught.exception))
        self.assertIn(DISTRIBUTION, str(caught.exception))

    def test_non_callable_object_raises(self) -> None:
        """
        An entry point resolving to a non-callable is rejected with context.
        """
        registry = Registry()
        with self.assertRaises(DefinitionError) as caught:
            registry.load_entry_points("a0svc-test.broken-object")
        self.assertIn("'constant'", str(caught.exception))

    def test_hook_errors_are_wrapped(self) -> None:
        """
        An exception inside a setup hook surfaces as DefinitionError.
        """
        registry = Registry()
        with self.assertRaises(DefinitionError) as caught:
            registry.load_entry_points("a0svc-test.broken-hook")
        self.assertIn("boom", str(caught.exception))

    def test_replace_semantics(self) -> None:
        """
        Colliding names follow the usual replace rules.
        """
        registry = Registry()
        registry.load_entry_points("a0svc-test.classes")
        with self.assertRaises(DuplicateServiceError):
            registry.load_entry_points("a0svc-test.classes")
        registry.load_entry_points("a0svc-test.classes", replace=True)
        self.assertEqual(len(registry), 1)

    def test_unknown_group_is_empty(self) -> None:
        """
        A group nothing advertises registers nothing.
        """
        registry = Registry()
        self.assertEqual(registry.load_entry_points("a0svc-test.no-such-group"), [])
        self.assertEqual(len(registry), 0)

    def test_closed_registry_raises(self) -> None:
        """
        A closed registry refuses to load entry points.
        """
        registry = Registry()
        registry.close()
        with self.assertRaises(ServiceError):
            registry.load_entry_points("a0svc-test.classes")
