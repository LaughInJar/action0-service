import asyncio
import io
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from action0.service import CircularDependencyError
from action0.service import Registry
from action0.service import Scope
from action0.service import ServiceError
from action0.service import injected


class Connection:
    """An async-built service tracking its disposal."""

    def __init__(self, dsn: str = "async://") -> None:
        """Store the connection string; not disposed yet."""
        self.dsn = dsn
        self.closed_via: str | None = None

    async def aclose(self) -> None:
        """Record async disposal."""
        self.closed_via = "aclose"

    def close(self) -> None:
        """Record sync disposal."""
        self.closed_via = "close"


class AsyncOnly:
    """A service that can only be disposed asynchronously."""

    def __init__(self) -> None:
        """Not disposed yet."""
        self.closed = False

    async def aclose(self) -> None:
        """Record async disposal."""
        self.closed = True


class Consumer:
    """A sync class depending on the async-built :py:class:`Connection`."""

    def __init__(self, connection: Connection) -> None:
        """Store the injected connection."""
        self.connection = connection


async def make_connection() -> Connection:
    """Async factory for :py:class:`Connection`."""
    await asyncio.sleep(0)
    return Connection()


class AsyncResolutionTestCase(unittest.IsolatedAsyncioTestCase):
    """aget()/abuild() resolve sync and async definitions."""

    async def test_aget_async_singleton(self) -> None:
        """An async factory builds once; aget returns the cached instance."""
        registry = Registry()
        registry.register(make_connection)
        first = await registry.aget(Connection)
        self.assertIsInstance(first, Connection)
        self.assertIs(await registry.aget(Connection), first)

    async def test_aget_async_transient(self) -> None:
        """Transient async services build fresh on every aget."""
        registry = Registry()
        registry.register(make_connection, scope=Scope.TRANSIENT)
        self.assertIsNot(await registry.aget(Connection), await registry.aget(Connection))

    async def test_aget_resolves_sync_definitions(self) -> None:
        """aget also serves plain sync registrations, sharing their cache."""
        registry = Registry()
        registry.register(Connection)
        instance = await registry.aget(Connection)
        # same singleton through the sync path
        self.assertIs(registry.get(Connection), instance)

    async def test_concurrent_first_aget_builds_once(self) -> None:
        """Concurrent first requests for a singleton are deduplicated."""
        builds = 0

        async def slow_factory() -> Connection:
            nonlocal builds
            builds += 1
            await asyncio.sleep(0.01)
            return Connection()

        registry = Registry()
        registry.register(slow_factory)
        instances = await asyncio.gather(*(registry.aget(Connection) for _ in range(5)))
        self.assertEqual(builds, 1)
        for instance in instances:
            self.assertIs(instance, instances[0])

    async def test_async_dependency_in_async_build(self) -> None:
        """A sync consumer of an async service resolves through aget."""
        registry = Registry()
        registry.register(make_connection)
        registry.register(Consumer)
        consumer = await registry.aget(Consumer)
        self.assertIsInstance(consumer.connection, Connection)

    async def test_abuild_unregistered_with_async_dependency(self) -> None:
        """abuild wires async dependencies into unregistered classes."""
        registry = Registry()
        registry.register(make_connection)
        consumer = await registry.abuild(Consumer)
        self.assertIsInstance(consumer.connection, Connection)

    async def test_afind_and_aget_all(self) -> None:
        """afind returns None when absent; aget_all collects instances."""
        registry = Registry()
        self.assertIsNone(await registry.afind(Connection))
        registry.register(make_connection)
        self.assertIsInstance(await registry.afind(Connection), Connection)
        self.assertEqual(len(await registry.aget_all(Connection)), 1)

    async def test_async_cycle_detected(self) -> None:
        """Cycle detection works through async builds."""

        class A:
            pass

        class B:
            pass

        async def make_a(b: B) -> A:
            return A()

        async def make_b(a: A) -> B:
            return B()

        registry = Registry()
        registry.register(make_a)
        registry.register(make_b)
        with self.assertRaises(CircularDependencyError):
            await registry.aget(A)


class SyncMismatchTestCase(unittest.TestCase):
    """Sync paths refuse async definitions with a clear error."""

    def test_get_of_async_definition_raises(self) -> None:
        """get() on an async factory points the caller at aget()."""
        registry = Registry()
        registry.register(make_connection)
        with self.assertRaisesRegex(ServiceError, "aget"):
            registry.get(Connection)

    def test_sync_build_with_async_dependency_raises(self) -> None:
        """The error also fires for an async service buried as a dependency."""
        registry = Registry()
        registry.register(make_connection)
        registry.register(Consumer)
        with self.assertRaisesRegex(ServiceError, "aget"):
            registry.get(Consumer)

    def test_close_skips_aclose_only_instance_with_warning(self) -> None:
        """Sync close() cannot await aclose(); it warns and skips."""
        registry = Registry()
        registry.register(AsyncOnly)
        instance = registry.get(AsyncOnly)
        with self.assertLogs("action0.service.registry", level="WARNING") as captured:
            registry.close()
        self.assertFalse(instance.closed)
        self.assertIn("aclose", captured.output[0])


class LazyAsyncTestCase(unittest.IsolatedAsyncioTestCase):
    """Lazily-loaded YAML definitions detect async factories on import.

    ``is_async`` is unknown until :py:meth:`Definition.materialize` runs, so
    the sync guard must materialize before deciding — a lazy async factory
    must not slip through ``get()`` as a bare coroutine object.
    """

    _tmp: tempfile.TemporaryDirectory  # type: ignore[type-arg]  # 3.11: not generic yet

    @classmethod
    def setUpClass(cls) -> None:
        """Generate an importable module holding an async factory."""
        cls._tmp = tempfile.TemporaryDirectory()
        module_path = Path(cls._tmp.name) / "a0svc_async_mod.py"
        module_path.write_text(
            textwrap.dedent(
                '''
                """Generated services for the lazy-async tests."""


                class Session:
                    pass


                async def make_session() -> Session:
                    return Session()
                '''
            )
        )
        sys.path.insert(0, cls._tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        """Drop the generated module from the path and clean up."""
        sys.path.remove(cls._tmp.name)
        sys.modules.pop("a0svc_async_mod", None)
        cls._tmp.cleanup()

    CATALOG = "session:\n  factory: a0svc_async_mod.make_session\n"

    async def test_aget_materializes_lazy_async_factory(self) -> None:
        """aget() imports the factory on first use and awaits it."""
        registry = Registry()
        registry.load_yaml(io.StringIO(self.CATALOG), lazy=True)
        instance = await registry.aget("session")
        self.assertEqual(type(instance).__name__, "Session")

    async def test_sync_get_of_lazy_async_factory_raises(self) -> None:
        """get() must refuse the factory even though is_async was unknown."""
        registry = Registry()
        registry.load_yaml(io.StringIO(self.CATALOG), lazy=True)
        with self.assertRaisesRegex(ServiceError, "aget"):
            registry.get("session")


class AsyncLifecycleTestCase(unittest.IsolatedAsyncioTestCase):
    """awarmup(), aclose(), and the async context manager."""

    async def test_awarmup_builds_eager_async_services(self) -> None:
        """awarmup instantiates eager definitions, async ones included."""
        registry = Registry()
        registry.register(make_connection, eager=True)
        instances = await registry.awarmup()
        self.assertEqual(len(instances), 1)
        self.assertIs(await registry.aget(Connection), instances[0])

    async def test_aclose_prefers_aclose_and_reverses_order(self) -> None:
        """aclose awaits aclose() over close(), dependents first."""
        order: list[str] = []

        class Tracked(Connection):
            async def aclose(self) -> None:
                await super().aclose()
                order.append("connection")

        class TrackedConsumer(Consumer):
            def close(self) -> None:
                order.append("consumer")

        registry = Registry()
        registry.register(Tracked, provides=Connection)
        registry.register(TrackedConsumer)
        consumer = await registry.aget(TrackedConsumer)
        await registry.aclose()
        self.assertEqual(order, ["consumer", "connection"])
        connection = consumer.connection
        assert isinstance(connection, Tracked)
        # aclose() won over the inherited sync close()
        self.assertEqual(connection.closed_via, "aclose")

    async def test_aclose_swallows_errors_and_closes_registry(self) -> None:
        """Disposal errors are logged; the registry still shuts down."""

        class Failing:
            async def aclose(self) -> None:
                raise RuntimeError("boom")

        registry = Registry()
        registry.register(Failing)
        registry.get(Failing)
        with self.assertLogs("action0.service.registry", level="ERROR"):
            await registry.aclose()
        with self.assertRaises(ServiceError):
            registry.get(Failing)

    async def test_async_context_manager_closes(self) -> None:
        """async with calls aclose() on exit."""
        async with Registry() as registry:
            registry.register(AsyncOnly)
            instance = await registry.aget(AsyncOnly)
        self.assertTrue(instance.closed)
        with self.assertRaises(ServiceError):
            registry.get(AsyncOnly)


class AsyncInjectTestCase(unittest.IsolatedAsyncioTestCase):
    """@registry.inject on async functions."""

    async def test_async_function_injection(self) -> None:
        """Sentinel parameters of an async function resolve via aget paths."""
        registry = Registry()
        registry.register(make_connection)

        @registry.inject
        async def handle(request: str, connection: Connection = injected) -> str:
            return f"{request} via {connection.dsn}"

        self.assertEqual(await handle("ping"), "ping via async://")

    async def test_async_injection_respects_explicit_argument(self) -> None:
        """Explicitly passed arguments are never overwritten."""
        registry = Registry()
        registry.register(make_connection)

        @registry.inject
        async def handle(connection: Connection = injected) -> Connection:
            return connection

        mine = Connection("mine://")
        self.assertIs(await handle(mine), mine)


if __name__ == "__main__":
    unittest.main()
