import unittest

from action0.service import AmbiguousServiceError
from action0.service import DefinitionError
from action0.service import DuplicateServiceError
from action0.service import Registry
from action0.service import Scope
from action0.service import ServiceError
from action0.service import ServiceNotFoundError


class Database:
    """A tiny service base class used throughout the tests."""

    def __init__(self, dsn: str = "sqlite://") -> None:
        """Store the connection string."""
        self.dsn = dsn


class Postgres(Database):
    """A subclass implementation of :py:class:`Database`."""

    def __init__(self, dsn: str = "pg://") -> None:
        """Store the connection string."""
        super().__init__(dsn)


class Mysql(Database):
    """Another subclass implementation of :py:class:`Database`."""

    def __init__(self, dsn: str = "mysql://") -> None:
        """Store the connection string."""
        super().__init__(dsn)


class Repository:
    """A service depending on :py:class:`Database`."""

    def __init__(self, db: Database) -> None:
        """Store the injected database."""
        self.db = db


def make_database() -> Database:
    """Return a database built by a factory function."""
    return Database("factory://")


class RegisterTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.service.registry.Registry.register` and friends
    """

    def test_register_class_and_get_by_type(self) -> None:
        """
        A registered class is returned for its own type, as a singleton.
        """
        registry = Registry()
        registry.register(Database)
        instance = registry.get(Database)
        self.assertIsInstance(instance, Database)
        self.assertIs(registry.get(Database), instance)

    def test_register_returns_definition(self) -> None:
        """
        The returned definition reflects the registration.
        """
        registry = Registry()
        definition = registry.register(
            Postgres, name="pg", scope=Scope.TRANSIENT, params={"dsn": "pg://x"}
        )
        self.assertIs(definition.provider, Postgres)
        self.assertIs(definition.provides, Postgres)
        self.assertEqual(definition.name, "pg")
        self.assertEqual(definition.scope, "transient")
        self.assertEqual(definition.params, {"dsn": "pg://x"})
        self.assertFalse(definition.default)  # named registrations are not default
        self.assertEqual(definition.label(), "Postgres (name='pg')")

    def test_register_with_params(self) -> None:
        """
        Configured params are applied to the constructor.
        """
        registry = Registry()
        registry.register(Database, params={"dsn": "pg://prod"})
        self.assertEqual(registry.get(Database).dsn, "pg://prod")

    def test_register_factory_with_return_annotation(self) -> None:
        """
        A factory's return annotation determines the provided type.
        """
        registry = Registry()
        registry.register(make_database)
        self.assertEqual(registry.get(Database).dsn, "factory://")

    def test_register_factory_without_annotation_raises(self) -> None:
        """
        A factory without return annotation needs an explicit ``provides``.
        """
        registry = Registry()
        with self.assertRaises(DefinitionError):
            registry.register(lambda: Database())
        registry.register(lambda: Database(), provides=Database)
        self.assertIsInstance(registry.get(Database), Database)

    def test_register_non_callable_raises(self) -> None:
        """
        Registering something that is not callable fails.
        """
        registry = Registry()
        with self.assertRaises(DefinitionError):
            registry.register("not a class")  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    def test_provides_must_be_a_superclass(self) -> None:
        """
        ``provides`` must be a base class of the registered class.
        """
        registry = Registry()
        with self.assertRaises(DefinitionError):
            registry.register(Database, provides=Repository)
        registry.register(Postgres, provides=Database)
        self.assertIsInstance(registry.get(Database), Postgres)

    def test_register_instance(self) -> None:
        """
        A registered instance is served as-is, by type and by name.
        """
        registry = Registry()
        database = Database("instance://")
        registry.register_instance(database, name="main")
        self.assertIs(registry.get("main"), database)
        self.assertIs(registry.get(Database), database)

    def test_register_instance_provides_mismatch_raises(self) -> None:
        """
        ``provides`` must match the instance's type.
        """
        registry = Registry()
        with self.assertRaises(DefinitionError):
            registry.register_instance(Database(), provides=Repository)

    def test_duplicate_unnamed_same_type(self) -> None:
        """
        Two unnamed registrations for the same type collide unless replaced.
        """
        registry = Registry()
        registry.register(Database)
        with self.assertRaises(DuplicateServiceError):
            registry.register(Database)
        registry.register(Database, params={"dsn": "pg://2"}, replace=True)
        self.assertEqual(registry.get(Database).dsn, "pg://2")
        self.assertEqual(len(registry), 1)

    def test_duplicate_name(self) -> None:
        """
        Two registrations under the same name collide unless replaced.
        """
        registry = Registry()
        registry.register(Postgres, name="db")
        with self.assertRaises(DuplicateServiceError):
            registry.register(Mysql, name="db")
        registry.register(Mysql, name="db", replace=True)
        self.assertIsInstance(registry.get("db"), Mysql)
        self.assertEqual(len(registry), 1)

    def test_service_decorator_bare(self) -> None:
        """
        The bare decorator registers the class unchanged.
        """
        registry = Registry()

        @registry.service
        class Widget:
            """A decorated service."""

        self.assertIsInstance(registry.get(Widget), Widget)

    def test_service_decorator_with_arguments(self) -> None:
        """
        The parameterized decorator forwards its arguments to register().
        """
        registry = Registry()

        @registry.service("widget.fast", scope=Scope.TRANSIENT)
        class Widget:
            """A decorated service."""

        first = registry.get("widget.fast")
        self.assertIsInstance(first, Widget)
        self.assertIsNot(registry.get("widget.fast"), first)


class LookupTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.service.registry.Registry.get` /
    :py:meth:`~action0.service.registry.Registry.find` /
    :py:meth:`~action0.service.registry.Registry.get_all`
    """

    def test_get_by_name(self) -> None:
        """
        Services are retrievable by their registration name.
        """
        registry = Registry()
        registry.register(Database, name="main")
        self.assertIsInstance(registry.get("main"), Database)

    def test_get_unknown_name_raises(self) -> None:
        """
        Unknown names raise :py:class:`ServiceNotFoundError`.
        """
        registry = Registry()
        with self.assertRaises(ServiceNotFoundError):
            registry.get("missing")

    def test_get_unknown_type_raises(self) -> None:
        """
        Types nothing provides raise :py:class:`ServiceNotFoundError`.
        """
        registry = Registry()
        with self.assertRaises(ServiceNotFoundError):
            registry.get(Database)

    def test_get_by_type_is_subclass_aware(self) -> None:
        """
        A service registered as a subclass answers base type requests.
        """
        registry = Registry()
        registry.register(Postgres)
        self.assertIsInstance(registry.get(Database), Postgres)

    def test_get_typed_and_named(self) -> None:
        """
        ``get(Type, name=...)`` returns the named service after a type check.
        """
        registry = Registry()
        registry.register(Postgres, name="pg")
        self.assertIsInstance(registry.get(Database, name="pg"), Postgres)
        with self.assertRaises(ServiceNotFoundError):
            registry.get(Repository, name="pg")  # wrong type

    def test_ambiguous_type_raises(self) -> None:
        """
        Several candidates without a clear default are an error.
        """
        registry = Registry()
        registry.register(Postgres, name="pg")
        registry.register(Mysql, name="my")
        with self.assertRaises(AmbiguousServiceError):
            registry.get(Database)

    def test_default_flag_wins_ambiguity(self) -> None:
        """
        A single definition marked default wins the type lookup.
        """
        registry = Registry()
        registry.register(Postgres, name="pg")
        registry.register(Mysql, name="my", default=True)
        self.assertIsInstance(registry.get(Database), Mysql)

    def test_unnamed_registration_is_the_default(self) -> None:
        """
        An unnamed registration beats named ones for its type.
        """
        registry = Registry()
        registry.register(Postgres, name="pg")
        registry.register(Mysql)
        self.assertIsInstance(registry.get(Database), Mysql)

    def test_exact_type_match_wins_ambiguity(self) -> None:
        """
        With several candidates, an exact type match beats subclass matches.
        """
        registry = Registry()
        registry.register(Database, name="base")
        registry.register(Postgres, name="pg")
        self.assertIs(type(registry.get(Database)), Database)

    def test_find_returns_none(self) -> None:
        """
        ``find`` returns ``None`` instead of raising for absent services.
        """
        registry = Registry()
        self.assertIsNone(registry.find(Database))
        self.assertIsNone(registry.find("missing"))

    def test_find_still_raises_on_ambiguity(self) -> None:
        """
        Ambiguity is a configuration problem, not an absence.
        """
        registry = Registry()
        registry.register(Postgres, name="pg")
        registry.register(Mysql, name="my")
        with self.assertRaises(AmbiguousServiceError):
            registry.find(Database)

    def test_get_all(self) -> None:
        """
        ``get_all`` returns one instance per matching definition.
        """
        registry = Registry()
        registry.register(Postgres, name="pg")
        registry.register(Mysql, name="my")
        instances = registry.get_all(Database)
        self.assertEqual([type(i) for i in instances], [Postgres, Mysql])

    def test_get_with_invalid_key_raises(self) -> None:
        """
        Keys must be types or strings.
        """
        registry = Registry()
        with self.assertRaises(TypeError):
            registry.get(42)  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]
        with self.assertRaises(TypeError):
            registry.get("name", name="other")  # type: ignore[call-overload]  # ty: ignore[invalid-argument-type]

    def test_dunders(self) -> None:
        """
        ``in``, ``len()``, iteration and ``repr()`` behave as expected.
        """
        registry = Registry()
        registry.register(Postgres, name="pg")
        self.assertIn("pg", registry)
        self.assertIn(Database, registry)
        self.assertNotIn("missing", registry)
        self.assertNotIn(Repository, registry)
        self.assertEqual(len(registry), 1)
        self.assertEqual([d.name for d in registry], ["pg"])
        self.assertEqual(registry.definitions()[0].name, "pg")
        self.assertIn("1 definitions", repr(registry))


class ParentChildTestCase(unittest.TestCase):
    """
    tests for parent/child registry layering
    """

    def test_child_falls_back_to_parent(self) -> None:
        """
        Lookups not satisfied locally consult the parent.
        """
        parent = Registry()
        parent.register(Database)
        child = Registry(parent=parent)
        self.assertIs(child.get(Database), parent.get(Database))

    def test_child_shadows_parent(self) -> None:
        """
        Local registrations win over the parent's.
        """
        parent = Registry()
        parent.register(Database)
        child = Registry(parent=parent)
        child.register(Postgres, provides=Database)
        self.assertIsInstance(child.get(Database), Postgres)
        self.assertIs(type(parent.get(Database)), Database)

    def test_parent_singletons_are_shared_between_children(self) -> None:
        """
        A parent-owned singleton is one instance for all children.
        """
        parent = Registry()
        parent.register(Database)
        first_child = Registry(parent=parent)
        second_child = Registry(parent=parent)
        self.assertIs(first_child.get(Database), second_child.get(Database))

    def test_parent_singletons_build_in_the_parent_context(self) -> None:
        """
        A child-first resolution must not capture child registrations in a
        shared parent singleton.
        """
        parent = Registry()
        parent.register(Database)
        parent.register(Repository)
        child = Registry(parent=parent)
        child.register_instance(Database("child://"), replace=True)
        repository = child.get(Repository)
        self.assertEqual(repository.db.dsn, "sqlite://")
        self.assertIs(parent.get(Repository), repository)

    def test_transient_services_use_the_child_wiring(self) -> None:
        """
        Non-cached services resolve dependencies through the requesting child.
        """
        parent = Registry()
        parent.register(Database)
        parent.register(Repository, scope=Scope.TRANSIENT)
        child = Registry(parent=parent)
        child.register_instance(Database("child://"), replace=True)
        self.assertEqual(child.get(Repository).db.dsn, "child://")
        self.assertEqual(parent.get(Repository).db.dsn, "sqlite://")

    def test_get_all_orders_parent_first(self) -> None:
        """
        ``get_all`` lists parent registrations before local ones.
        """
        parent = Registry()
        parent.register(Postgres, name="pg")
        child = Registry(parent=parent)
        child.register(Mysql, name="my")
        self.assertEqual([type(i) for i in child.get_all(Database)], [Postgres, Mysql])


class OverrideTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.service.registry.Registry.override`
    """

    def test_override_by_type(self) -> None:
        """
        A type override wins every lookup while active, then is removed.
        """
        registry = Registry()
        registry.register(Database)
        real = registry.get(Database)
        fake = Database("fake://")
        with registry.override(Database, fake):
            self.assertIs(registry.get(Database), fake)
        self.assertIs(registry.get(Database), real)

    def test_override_by_name(self) -> None:
        """
        A name override replaces the named service while active.
        """
        registry = Registry()
        registry.register(Database, name="main")
        fake = Database("fake://")
        with registry.override("main", fake):
            self.assertIs(registry.get("main"), fake)
            self.assertIs(registry.get(Database, name="main"), fake)
        self.assertIsNot(registry.get("main"), fake)

    def test_override_reaches_injection(self) -> None:
        """
        Services built while an override is active get the replacement.
        """
        registry = Registry()
        registry.register(Database)
        registry.register(Repository, scope=Scope.TRANSIENT)
        fake = Database("fake://")
        with registry.override(Database, fake):
            self.assertIs(registry.get(Repository).db, fake)
        self.assertIsNot(registry.get(Repository).db, fake)

    def test_override_shadows_in_get_all(self) -> None:
        """
        A name override replaces the shadowed definition in ``get_all``.
        """
        registry = Registry()
        registry.register(Postgres, name="pg")
        fake = Mysql("fake://")
        with registry.override("pg", fake):
            self.assertEqual(registry.get_all(Database), [fake])

    def test_overrides_nest(self) -> None:
        """
        The innermost override wins; unwinding restores the outer one.
        """
        registry = Registry()
        registry.register(Database)
        outer, inner = Database("outer://"), Database("inner://")
        with registry.override(Database, outer):
            with registry.override(Database, inner):
                self.assertIs(registry.get(Database), inner)
            self.assertIs(registry.get(Database), outer)


class BuildTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.service.registry.Registry.build`
    """

    def test_build_injects_dependencies(self) -> None:
        """
        ``build`` wires an unregistered class without registering it.
        """
        registry = Registry()
        registry.register(Database)
        repository = registry.build(Repository)
        self.assertIs(repository.db, registry.get(Database))
        self.assertNotIn(Repository, registry)

    def test_build_params_override_injection(self) -> None:
        """
        Explicit keyword arguments win over injection.
        """
        registry = Registry()
        registry.register(Database)
        mine = Database("mine://")
        self.assertIs(registry.build(Repository, db=mine).db, mine)

    def test_build_never_caches(self) -> None:
        """
        Every call constructs a fresh instance.
        """
        registry = Registry()
        registry.register(Database)
        self.assertIsNot(registry.build(Repository), registry.build(Repository))


class ClosedRegistryTestCase(unittest.TestCase):
    """
    tests for using a registry after :py:meth:`~action0.service.registry.Registry.close`
    """

    def test_use_after_close_raises(self) -> None:
        """
        Every entry point refuses to work on a closed registry.
        """
        registry = Registry()
        registry.register(Database)
        registry.close()
        with self.assertRaises(ServiceError):
            registry.get(Database)
        with self.assertRaises(ServiceError):
            registry.register(Repository)
        with self.assertRaises(ServiceError):
            registry.build(Repository)
        with self.assertRaises(ServiceError):
            registry.find(Database)
