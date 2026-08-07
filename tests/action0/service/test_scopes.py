import contextvars
import threading
import time
import unittest
from collections.abc import Callable
from typing import Any

from action0.service import Registry
from action0.service import Scope
from action0.service import ScopeError
from action0.service import ScopePolicy
from action0.service.definitions import Definition


class Widget:
    """A trivial service for scope tests."""


class SlowWidget:
    """A service with a slow, call-counting constructor."""

    calls = 0

    def __init__(self) -> None:
        """Count the call and simulate expensive construction."""
        SlowWidget.calls += 1
        time.sleep(0.02)


class CountingScope(ScopePolicy):
    """A custom scope that builds every time and counts the requests."""

    caches = False

    def __init__(self) -> None:
        """Set up the counter."""
        self.requests = 0

    def get(self, definition: Definition, build: Callable[[], Any]) -> Any:
        """Count and build."""
        self.requests += 1
        return build()


class SingletonScopeTestCase(unittest.TestCase):
    """
    tests for the ``singleton`` scope
    """

    def test_singleton_is_shared(self) -> None:
        """
        Repeated gets return the same instance.
        """
        registry = Registry()
        registry.register(Widget)
        self.assertIs(registry.get(Widget), registry.get(Widget))

    def test_singletons_are_per_registry(self) -> None:
        """
        Independent registries hold independent singletons.
        """
        first, second = Registry(), Registry()
        first.register(Widget)
        second.register(Widget)
        self.assertIsNot(first.get(Widget), second.get(Widget))

    def test_concurrent_first_get_builds_once(self) -> None:
        """
        Racing threads must not build the singleton twice.
        """
        SlowWidget.calls = 0
        registry = Registry()
        registry.register(SlowWidget)
        barrier = threading.Barrier(8)
        instances: list[SlowWidget] = []
        lock = threading.Lock()

        def worker() -> None:
            """Wait for all threads, then resolve the singleton."""
            barrier.wait()
            instance = registry.get(SlowWidget)
            with lock:
                instances.append(instance)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(SlowWidget.calls, 1)
        self.assertEqual(len(set(id(instance) for instance in instances)), 1)


class TransientScopeTestCase(unittest.TestCase):
    """
    tests for the ``transient`` scope
    """

    def test_transient_builds_every_time(self) -> None:
        """
        Every get returns a fresh instance.
        """
        registry = Registry()
        registry.register(Widget, scope=Scope.TRANSIENT)
        self.assertIsNot(registry.get(Widget), registry.get(Widget))


class ThreadScopeTestCase(unittest.TestCase):
    """
    tests for the ``thread`` scope
    """

    def test_same_thread_shares_the_instance(self) -> None:
        """
        Within one thread the instance is cached.
        """
        registry = Registry()
        registry.register(Widget, scope=Scope.THREAD)
        self.assertIs(registry.get(Widget), registry.get(Widget))

    def test_other_threads_get_their_own_instance(self) -> None:
        """
        Each thread holds its own instance.
        """
        registry = Registry()
        registry.register(Widget, scope="thread")
        local_instance = registry.get(Widget)
        remote_instances: list[Widget] = []

        def worker() -> None:
            """Resolve the widget twice from another thread."""
            remote_instances.append(registry.get(Widget))
            remote_instances.append(registry.get(Widget))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertIs(remote_instances[0], remote_instances[1])
        self.assertIsNot(remote_instances[0], local_instance)


class ContextScopeTestCase(unittest.TestCase):
    """
    tests for the ``context`` scope
    """

    def test_same_context_shares_the_instance(self) -> None:
        """
        Within one context the instance is cached.
        """
        registry = Registry()
        registry.register(Widget, scope=Scope.CONTEXT)
        self.assertIs(registry.get(Widget), registry.get(Widget))

    def test_separate_contexts_get_their_own_instance(self) -> None:
        """
        An instance built inside a context copy stays inside it.
        """
        registry = Registry()
        registry.register(Widget, scope=Scope.CONTEXT)
        context = contextvars.copy_context()
        inside = context.run(registry.get, Widget)
        outside = registry.get(Widget)
        self.assertIsNot(inside, outside)
        self.assertIs(context.run(registry.get, Widget), inside)

    def test_context_copies_inherit_existing_instances(self) -> None:
        """
        Copying a context after creation shares the instance (contextvars
        semantics: copies inherit current values).
        """
        registry = Registry()
        registry.register(Widget, scope=Scope.CONTEXT)
        outside = registry.get(Widget)
        context = contextvars.copy_context()
        self.assertIs(context.run(registry.get, Widget), outside)


class CustomScopeTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.service.registry.Registry.register_scope`
    """

    def test_custom_scope_is_used(self) -> None:
        """
        Definitions can use a registered custom scope by key.
        """
        registry = Registry()
        counting = CountingScope()
        registry.register_scope("counting", counting)
        registry.register(Widget, scope="counting")
        first, second = registry.get(Widget), registry.get(Widget)
        self.assertIsNot(first, second)
        self.assertEqual(counting.requests, 2)

    def test_unknown_scope_raises_at_registration(self) -> None:
        """
        Registering with an unknown scope key fails early.
        """
        registry = Registry()
        with self.assertRaises(ScopeError):
            registry.register(Widget, scope="request")

    def test_enum_and_string_are_interchangeable(self) -> None:
        """
        ``Scope.THREAD`` and ``"thread"`` mean the same scope.
        """
        registry = Registry()
        definition = registry.register(Widget, scope=Scope.THREAD)
        self.assertEqual(definition.scope, "thread")
