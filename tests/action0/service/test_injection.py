from typing import Annotated
from typing import Any
from typing import Optional
from unittest import TestCase

from action0.service import CircularDependencyError
from action0.service import DefinitionError
from action0.service import InjectionError
from action0.service import Named
from action0.service import Ref
from action0.service import Registry
from action0.service import ServiceNotFoundError
from action0.service import injected


class Database:
    """A tiny service used as an injection target."""

    def __init__(self, dsn: str = "sqlite://") -> None:
        """Store the connection string."""
        self.dsn = dsn


class Cache:
    """A second injectable service."""

    def __init__(self, size: int = 16) -> None:
        """Store the size."""
        self.size = size


class Consumer:
    """A service depending on :py:class:`Database`."""

    def __init__(self, db: Database) -> None:
        """Store the injected database."""
        self.db = db


class OptionalConsumer:
    """A service optionally depending on :py:class:`Cache`."""

    def __init__(self, cache: Optional[Cache] = None) -> None:
        """Store the injected cache, if any."""
        self.cache = cache


class PipeConsumer:
    """Like :py:class:`OptionalConsumer` but with ``X | None`` syntax."""

    def __init__(self, cache: "Cache | None") -> None:
        """Store the injected cache, if any."""
        self.cache = cache


class DefaultedConsumer:
    """A service whose dependency parameter has a real default."""

    def __init__(self, db: Database = Database("default://")) -> None:
        """Store the database."""
        self.db = db


class NamedConsumer:
    """A service requesting a specific named database."""

    def __init__(self, db: Annotated[Database, Named("replica")]) -> None:
        """Store the injected database."""
        self.db = db


class KwargsConsumer:
    """A service accepting arbitrary extra parameters."""

    def __init__(self, db: Database, **extra: Any) -> None:
        """Store everything."""
        self.db = db
        self.extra = extra


class PositionalOnlyConsumer:
    """A service with a positional-only dependency."""

    def __init__(self, db: Database, /, label: str = "x") -> None:
        """Store the injected database."""
        self.db = db
        self.label = label


class NoAnnotationConsumer:
    """A service with an unannotated required parameter."""

    def __init__(self, mystery) -> None:  # type: ignore[no-untyped-def]
        """Store the parameter."""
        self.mystery = mystery


class Selfish:
    """A service depending on its own type."""

    def __init__(self, other: "Selfish") -> None:
        """Never reached."""


class LoopA:
    """One half of a dependency cycle."""

    def __init__(self, other: "LoopB") -> None:
        """Store the other half."""
        self.other = other


class LoopB:
    """The other half of a dependency cycle."""

    def __init__(self, other: LoopA) -> None:
        """Store the other half."""
        self.other = other


class ParameterResolutionTestCase(TestCase):
    """
    tests for how constructor parameters are resolved
    """

    def test_params_win_over_registry(self) -> None:
        """
        A configured param beats a registered service.
        """
        registry = Registry()
        registry.register(Database)
        mine = Database("mine://")
        registry.register(Consumer, params={"db": mine})
        self.assertIs(registry.get(Consumer).db, mine)

    def test_registered_service_wins_over_provider_default(self) -> None:
        """
        A matching registration beats the parameter's default value.
        """
        registry = Registry()
        registry.register(Database)
        registry.register(DefaultedConsumer)
        self.assertIs(registry.get(DefaultedConsumer).db, registry.get(Database))

    def test_provider_default_used_when_nothing_registered(self) -> None:
        """
        Without a matching registration the default value applies.
        """
        registry = Registry()
        registry.register(DefaultedConsumer)
        self.assertEqual(registry.get(DefaultedConsumer).db.dsn, "default://")

    def test_optional_dependency_none_when_absent(self) -> None:
        """
        ``Optional[X]`` parameters become ``None`` when X is unregistered.
        """
        registry = Registry()
        registry.register(OptionalConsumer)
        registry.register(PipeConsumer)
        self.assertIsNone(registry.get(OptionalConsumer).cache)
        self.assertIsNone(registry.get(PipeConsumer).cache)

    def test_optional_dependency_injected_when_present(self) -> None:
        """
        ``Optional[X]`` parameters are injected when X is registered.
        """
        registry = Registry()
        registry.register(Cache)
        registry.register(OptionalConsumer)
        self.assertIs(registry.get(OptionalConsumer).cache, registry.get(Cache))

    def test_named_qualifier(self) -> None:
        """
        ``Annotated[X, Named(...)]`` injects the named service.
        """
        registry = Registry()
        registry.register(Database)
        registry.register(Database, name="replica", params={"dsn": "replica://"})
        registry.register(NamedConsumer)
        self.assertEqual(registry.get(NamedConsumer).db.dsn, "replica://")

    def test_named_qualifier_missing_raises(self) -> None:
        """
        A missing named service is an injection error.
        """
        registry = Registry()
        registry.register(NamedConsumer)
        with self.assertRaises(InjectionError):
            registry.get(NamedConsumer)

    def test_primitive_types_are_not_injected(self) -> None:
        """
        Builtin value types never come from the registry.
        """
        registry = Registry()
        registry.register_instance("configured", name="some-string")
        registry.register(Database)
        self.assertEqual(registry.get(Database).dsn, "sqlite://")

    def test_missing_required_dependency_raises(self) -> None:
        """
        A required parameter without value, default, or service raises.
        """
        registry = Registry()
        registry.register(Consumer)
        with self.assertRaises(InjectionError):
            registry.get(Consumer)

    def test_unannotated_required_parameter_raises(self) -> None:
        """
        A required parameter without annotation cannot be resolved.
        """
        registry = Registry()
        registry.register(NoAnnotationConsumer)
        with self.assertRaises(InjectionError):
            registry.get(NoAnnotationConsumer)
        registry.register(NoAnnotationConsumer, params={"mystery": 42}, replace=True)
        self.assertEqual(registry.get(NoAnnotationConsumer).mystery, 42)

    def test_unknown_param_key_raises(self) -> None:
        """
        Params that do not exist on the provider are a definition error.
        """
        registry = Registry()
        registry.register(Database, params={"nope": 1})
        with self.assertRaises(DefinitionError):
            registry.get(Database)

    def test_var_keyword_provider_accepts_extra_params(self) -> None:
        """
        Extra params flow into ``**kwargs`` providers.
        """
        registry = Registry()
        registry.register(Database)
        registry.register(KwargsConsumer, params={"tag": "a", "level": 3})
        consumer = registry.get(KwargsConsumer)
        self.assertIs(consumer.db, registry.get(Database))
        self.assertEqual(consumer.extra, {"tag": "a", "level": 3})

    def test_positional_only_parameter_is_injected(self) -> None:
        """
        Positional-only dependencies are passed positionally.
        """
        registry = Registry()
        registry.register(Database)
        registry.register(PositionalOnlyConsumer)
        self.assertIs(registry.get(PositionalOnlyConsumer).db, registry.get(Database))


class RefTestCase(TestCase):
    """
    tests for :py:class:`action0.service.markers.Ref` parameter values
    """

    def test_ref_by_name(self) -> None:
        """
        A ``Ref("name")`` param value resolves to the named service.
        """
        registry = Registry()
        registry.register(Database, name="main")
        registry.register(Consumer, params={"db": Ref("main")})
        self.assertIs(registry.get(Consumer).db, registry.get("main"))

    def test_ref_by_type(self) -> None:
        """
        A ``Ref(Type)`` param value resolves the default implementation.
        """
        registry = Registry()
        registry.register(Database)
        registry.register(Consumer, params={"db": Ref(Database)})
        self.assertIs(registry.get(Consumer).db, registry.get(Database))

    def test_ref_inside_containers(self) -> None:
        """
        Refs nested in lists and dicts are resolved too.
        """
        registry = Registry()
        registry.register(Database, name="main")
        registry.register(
            KwargsConsumer,
            params={"db": Ref("main"), "pool": [Ref("main")], "map": {"db": Ref("main")}},
        )
        consumer = registry.get(KwargsConsumer)
        database = registry.get("main")
        self.assertEqual(consumer.extra["pool"], [database])
        self.assertEqual(consumer.extra["map"], {"db": database})

    def test_dangling_ref_raises(self) -> None:
        """
        A ref to an unknown service raises at build time.
        """
        registry = Registry()
        registry.register(Consumer, params={"db": Ref("missing")})
        with self.assertRaises(ServiceNotFoundError):
            registry.get(Consumer)


class CircularDependencyTestCase(TestCase):
    """
    tests for cycle detection during builds
    """

    def test_cycle_raises_with_chain(self) -> None:
        """
        A dependency cycle raises and names the chain.
        """
        registry = Registry()
        registry.register(LoopA)
        registry.register(LoopB)
        with self.assertRaises(CircularDependencyError) as caught:
            registry.get(LoopA)
        self.assertIn("LoopA -> LoopB -> LoopA", str(caught.exception))

    def test_self_dependency_raises(self) -> None:
        """
        A service depending on itself is the smallest cycle.
        """
        registry = Registry()
        registry.register(Selfish)
        with self.assertRaises(CircularDependencyError):
            registry.get(Selfish)


class InjectDecoratorTestCase(TestCase):
    """
    tests for :py:meth:`action0.service.registry.Registry.inject`
    """

    def test_sentinel_parameters_are_filled(self) -> None:
        """
        Parameters defaulting to ``injected`` are resolved at call time.
        """
        registry = Registry()
        registry.register(Database)

        @registry.inject
        def handler(tag: str, db: Database = injected) -> str:
            """Combine the tag with the database dsn."""
            return f"{tag}:{db.dsn}"

        self.assertEqual(handler("x"), "x:sqlite://")

    def test_caller_arguments_win(self) -> None:
        """
        Explicitly passed arguments are not replaced.
        """
        registry = Registry()
        registry.register(Database)

        @registry.inject
        def handler(db: Database = injected) -> str:
            """Return the database dsn."""
            return db.dsn

        self.assertEqual(handler(Database("mine://")), "mine://")
        self.assertEqual(handler(db=Database("kw://")), "kw://")

    def test_explicitly_passing_the_sentinel_resolves(self) -> None:
        """
        Passing ``injected`` explicitly requests resolution as well.
        """
        registry = Registry()
        registry.register(Database)

        @registry.inject
        def handler(db: Database = injected) -> str:
            """Return the database dsn."""
            return db.dsn

        self.assertEqual(handler(injected), "sqlite://")

    def test_named_qualifier_in_functions(self) -> None:
        """
        ``Annotated[X, Named(...)]`` works on function parameters too.
        """
        registry = Registry()
        registry.register(Database, name="replica", params={"dsn": "replica://"})

        @registry.inject
        def handler(db: Annotated[Database, Named("replica")] = injected) -> str:
            """Return the database dsn."""
            return db.dsn

        self.assertEqual(handler(), "replica://")

    def test_unresolvable_sentinel_raises_at_call_time(self) -> None:
        """
        Decoration succeeds; the injection error happens on the call.
        """
        registry = Registry()

        @registry.inject
        def handler(db: Database = injected) -> str:
            """Return the database dsn."""
            return db.dsn

        with self.assertRaises(InjectionError):
            handler()

    def test_non_sentinel_parameters_are_untouched(self) -> None:
        """
        Parameters with real defaults are never injected.
        """
        registry = Registry()
        registry.register(Database)

        @registry.inject
        def handler(limit: int = 10, db: Database = injected) -> str:
            """Combine the limit with the database dsn."""
            return f"{limit}:{db.dsn}"

        self.assertEqual(handler(), "10:sqlite://")
