import unittest
from typing import Optional

from action0.service import Ref
from action0.service import Registry
from action0.service import Scope
from action0.service import ServiceError
from action0.service import ValidationError


class Connection:
    """A closable low-level service."""

    def __init__(self) -> None:
        """Start open."""
        self.closed = False

    def close(self) -> None:
        """Mark the connection closed and record the event."""
        self.closed = True
        _CLOSE_ORDER.append("connection")


class Pool:
    """A closable service depending on :py:class:`Connection`."""

    def __init__(self, connection: Connection) -> None:
        """Store the injected connection."""
        self.connection = connection

    def close(self) -> None:
        """Record the close event."""
        _CLOSE_ORDER.append("pool")


class Broken:
    """A service whose ``close`` raises."""

    def close(self) -> None:
        """Fail on purpose."""
        raise RuntimeError("broken close")


class First:
    """One half of a static dependency cycle."""

    def __init__(self, other: "Second") -> None:
        """Never reached."""


class Second:
    """The other half of a static dependency cycle."""

    def __init__(self, other: First) -> None:
        """Never reached."""


_CLOSE_ORDER: list[str] = []


class CloseTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.service.registry.Registry.close`
    """

    def setUp(self) -> None:
        """Reset the recorded close order."""
        _CLOSE_ORDER.clear()

    def test_close_disposes_in_reverse_creation_order(self) -> None:
        """
        Dependents are closed before their dependencies.
        """
        registry = Registry()
        registry.register(Connection)
        registry.register(Pool)
        registry.get(Pool)  # builds Connection first, then Pool
        registry.close()
        self.assertEqual(_CLOSE_ORDER, ["pool", "connection"])

    def test_registered_instances_are_not_closed(self) -> None:
        """
        The registry only disposes instances it created itself.
        """
        registry = Registry()
        outside = Connection()
        registry.register_instance(outside)
        registry.get(Connection)
        registry.close()
        self.assertFalse(outside.closed)

    def test_close_is_idempotent(self) -> None:
        """
        A second close does nothing.
        """
        registry = Registry()
        registry.register(Connection)
        registry.get(Connection)
        registry.close()
        registry.close()
        self.assertEqual(_CLOSE_ORDER, ["connection"])

    def test_close_logs_and_continues_on_errors(self) -> None:
        """
        A failing ``close`` is logged; the remaining instances still close.
        """
        registry = Registry()
        registry.register(Connection)
        registry.register(Broken)
        registry.get(Connection)
        registry.get(Broken)
        with self.assertLogs("action0.service.registry", level="ERROR"):
            registry.close()
        self.assertEqual(_CLOSE_ORDER, ["connection"])

    def test_context_manager_closes(self) -> None:
        """
        Leaving the ``with`` block closes the registry.
        """
        with Registry() as registry:
            registry.register(Connection)
            connection = registry.get(Connection)
        self.assertTrue(connection.closed)
        with self.assertRaises(ServiceError):
            registry.get(Connection)


class WarmupTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.service.registry.Registry.warmup`
    """

    def test_warmup_builds_eager_definitions_only(self) -> None:
        """
        Only ``eager=True`` definitions are instantiated.
        """
        built: list[str] = []

        class Eager:
            """An eagerly-built service."""

            def __init__(self) -> None:
                """Record the build."""
                built.append("eager")

        class Lazy:
            """A lazily-built service."""

            def __init__(self) -> None:
                """Record the build."""
                built.append("lazy")

        registry = Registry()
        registry.register(Eager, eager=True)
        registry.register(Lazy)
        instances = registry.warmup()
        self.assertEqual(built, ["eager"])
        self.assertEqual(len(instances), 1)
        self.assertIs(instances[0], registry.get(Eager))


class ValidateTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.service.registry.Registry.validate`
    """

    def test_valid_configuration_passes(self) -> None:
        """
        A resolvable configuration validates silently.
        """
        registry = Registry()
        registry.register(Connection)
        registry.register(Pool)
        registry.validate()

    def test_unknown_param_is_reported(self) -> None:
        """
        Params that do not exist on the provider are reported.
        """
        registry = Registry()
        registry.register(Connection, params={"nope": 1})
        with self.assertRaises(ValidationError) as caught:
            registry.validate()
        self.assertIn("unknown init parameter 'nope'", str(caught.exception))

    def test_missing_dependency_is_reported(self) -> None:
        """
        Required parameters nothing can satisfy are reported.
        """
        registry = Registry()
        registry.register(Pool)  # Connection is not registered
        with self.assertRaises(ValidationError) as caught:
            registry.validate()
        self.assertIn("'connection'", str(caught.exception))

    def test_dangling_ref_is_reported(self) -> None:
        """
        Refs to unknown services are reported.
        """
        registry = Registry()
        registry.register(Connection)
        registry.register(Pool, params={"connection": Ref("missing")})
        with self.assertRaises(ValidationError) as caught:
            registry.validate()
        self.assertIn("missing", str(caught.exception))

    def test_dependency_cycle_is_reported(self) -> None:
        """
        Static cycle detection finds cycles without instantiating.
        """
        registry = Registry()
        registry.register(First)
        registry.register(Second)
        with self.assertRaises(ValidationError) as caught:
            registry.validate()
        self.assertIn("dependency cycle", str(caught.exception))

    def test_all_problems_are_collected(self) -> None:
        """
        Every problem is listed, not just the first one.
        """

        class Needy:
            """A service with an unsatisfiable dependency."""

            def __init__(self, pool: Pool) -> None:
                """Never reached."""

        registry = Registry()
        registry.register(Connection, params={"nope": 1})
        registry.register(Needy)
        with self.assertRaises(ValidationError) as caught:
            registry.validate()
        self.assertEqual(len(caught.exception.problems), 2)

    def test_optional_missing_dependency_is_fine(self) -> None:
        """
        Optional parameters do not need a registration.
        """

        class Tolerant:
            """A service with an optional dependency."""

            def __init__(self, pool: Optional[Pool] = None) -> None:
                """Store the pool."""
                self.pool = pool

        registry = Registry()
        registry.register(Tolerant)
        registry.validate()


class ThreadAndContextDisposalTestCase(unittest.TestCase):
    """
    tests for disposal of thread- and context-scoped instances
    """

    def test_close_disposes_visible_scoped_instances(self) -> None:
        """
        close() disposes what the calling thread/context can see.
        """
        registry = Registry()
        registry.register(Connection, scope=Scope.THREAD)
        connection = registry.get(Connection)
        registry.close()
        self.assertTrue(connection.closed)
