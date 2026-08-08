import threading
import unittest

from action0.service import Registry
from action0.service import default_registry
from action0.service import set_default_registry
from action0.service import using_default_registry


class Widget:
    """A trivial service for default-registry tests."""


class DefaultRegistryTestCase(unittest.TestCase):
    """
    tests for :py:mod:`action0.service.default`
    """

    def setUp(self) -> None:
        """Start each test from a reset default, remembering the previous one."""
        self._previous = set_default_registry(None)

    def tearDown(self) -> None:
        """Restore whatever default was installed before the test."""
        set_default_registry(self._previous)

    def test_created_lazily_and_shared(self) -> None:
        """
        The first access creates the instance; later accesses return it.
        """
        first = default_registry()
        self.assertIsInstance(first, Registry)
        self.assertIs(default_registry(), first)

    def test_is_a_fully_functional_registry(self) -> None:
        """
        The default registry registers and resolves like any other.
        """
        default_registry().register(Widget)
        self.assertIsInstance(default_registry().get(Widget), Widget)

    def test_set_returns_the_previous_instance(self) -> None:
        """
        Installing a replacement hands back what was installed before.
        """
        original = default_registry()
        replacement = Registry()
        self.assertIs(set_default_registry(replacement), original)
        self.assertIs(default_registry(), replacement)

    def test_set_none_resets(self) -> None:
        """
        After a reset the next access creates a fresh instance.
        """
        original = default_registry()
        self.assertIs(set_default_registry(None), original)
        self.assertIsNot(default_registry(), original)

    def test_using_default_registry_swaps_and_restores(self) -> None:
        """
        The context manager installs the registry and restores the previous.
        """
        original = default_registry()
        temporary = Registry()
        with using_default_registry(temporary) as active:
            self.assertIs(active, temporary)
            self.assertIs(default_registry(), temporary)
        self.assertIs(default_registry(), original)

    def test_using_default_registry_restores_on_error(self) -> None:
        """
        The previous default comes back even when the block raises.
        """
        original = default_registry()
        with self.assertRaises(RuntimeError):
            with using_default_registry(Registry()):
                raise RuntimeError("boom")
        self.assertIs(default_registry(), original)

    def test_using_default_registry_restores_the_unset_state(self) -> None:
        """
        Used before any default exists, the context restores "not created
        yet": the instance built afterwards is a fresh one.
        """
        temporary = Registry()
        with using_default_registry(temporary):
            self.assertIs(default_registry(), temporary)
        self.assertIsNot(default_registry(), temporary)

    def test_concurrent_first_access_creates_one_instance(self) -> None:
        """
        Racing threads all observe the same lazily-created instance.
        """
        barrier = threading.Barrier(8)
        seen: list[Registry] = []
        lock = threading.Lock()

        def worker() -> None:
            """Wait for all threads, then fetch the default registry."""
            barrier.wait()
            registry = default_registry()
            with lock:
                seen.append(registry)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(set(id(registry) for registry in seen)), 1)
