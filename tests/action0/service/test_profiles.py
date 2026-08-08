"""Tests for profile-limited registrations and profile-aware resolution."""

import asyncio
import io
import unittest

from action0.service import Registry
from action0.service.errors import AmbiguousServiceError
from action0.service.errors import DefinitionError
from action0.service.errors import DuplicateServiceError
from action0.service.errors import ServiceNotFoundError
from action0.service.errors import ValidationError


class Database:
    def __init__(self, dsn: str = "sqlite://"):
        self.dsn = dsn


class DevDatabase(Database):
    pass


class ProdDatabase(Database):
    pass


class Consumer:
    def __init__(self, database: Database):
        self.database = database


class RegistryProfilesTestCase(unittest.TestCase):
    def test_profiles_default_empty(self) -> None:
        self.assertEqual(Registry().profiles, frozenset())

    def test_profiles_stored_as_frozenset(self) -> None:
        registry = Registry(profiles=["dev", "dev"])
        self.assertEqual(registry.profiles, frozenset({"dev"}))

    def test_bare_string_is_one_profile(self) -> None:
        # not the iterable-of-characters footgun
        self.assertEqual(Registry(profiles="dev").profiles, frozenset({"dev"}))

    def test_profiles_property_is_read_only(self) -> None:
        registry = Registry(profiles=["dev"])
        with self.assertRaises(AttributeError):
            registry.profiles = frozenset({"prod"})  # type: ignore[misc]  # ty: ignore[invalid-assignment]

    def test_child_inherits_parent_profiles(self) -> None:
        parent = Registry(profiles=["dev"])
        child = Registry(parent=parent)
        self.assertEqual(child.profiles, frozenset({"dev"}))

    def test_child_profiles_override_parent(self) -> None:
        parent = Registry(profiles=["dev"])
        child = Registry(parent=parent, profiles=["prod"])
        self.assertEqual(child.profiles, frozenset({"prod"}))

    def test_child_empty_profiles_override_parent(self) -> None:
        parent = Registry(profiles=["dev"])
        child = Registry(parent=parent, profiles=[])
        self.assertEqual(child.profiles, frozenset())


class ConditionalRegistrationTestCase(unittest.TestCase):
    def test_named_variants_resolved_per_profile(self) -> None:
        for profile, expected in (("dev", DevDatabase), ("prod", ProdDatabase)):
            registry = Registry(profiles=[profile])
            registry.register(DevDatabase, name="db", profiles=["dev"])
            registry.register(ProdDatabase, name="db", profiles=["prod"])
            self.assertIsInstance(registry.get("db"), expected)

    def test_unnamed_variants_resolved_per_profile(self) -> None:
        registry = Registry(profiles=["prod"])
        registry.register(Database, provides=Database, profiles=["dev"])
        registry.register(ProdDatabase, provides=Database, profiles=["prod"])
        self.assertIsInstance(registry.get(Database), ProdDatabase)

    def test_universal_definition_active_everywhere(self) -> None:
        for profiles in (None, ["dev"], ["anything"]):
            registry = Registry(profiles=profiles)
            registry.register(Database, name="db")
            self.assertIsInstance(registry.get("db"), Database)

    def test_disjoint_profiles_may_share_a_name(self) -> None:
        registry = Registry()
        registry.register(DevDatabase, name="db", profiles=["dev"])
        registry.register(ProdDatabase, name="db", profiles=["prod"])
        self.assertEqual(len(registry), 2)

    def test_overlapping_profiles_collide(self) -> None:
        registry = Registry()
        registry.register(DevDatabase, name="db", profiles=["dev", "staging"])
        with self.assertRaises(DuplicateServiceError):
            registry.register(ProdDatabase, name="db", profiles=["staging"])

    def test_universal_collides_with_profiled(self) -> None:
        registry = Registry()
        registry.register(DevDatabase, name="db", profiles=["dev"])
        with self.assertRaises(DuplicateServiceError):
            registry.register(Database, name="db")

    def test_profiled_collides_with_universal(self) -> None:
        registry = Registry()
        registry.register(Database, name="db")
        with self.assertRaises(DuplicateServiceError):
            registry.register(DevDatabase, name="db", profiles=["dev"])

    def test_replace_removes_only_overlapping(self) -> None:
        registry = Registry(profiles=["prod"])
        registry.register(DevDatabase, name="db", profiles=["dev"])
        registry.register(ProdDatabase, name="db", profiles=["prod"])
        registry.register(Database, name="db", replace=True)  # overlaps both
        self.assertEqual(len(registry), 1)
        self.assertIsInstance(registry.get("db"), Database)

    def test_decorator_form_accepts_profiles(self) -> None:
        registry = Registry(profiles=["dev"])

        @registry.service("clock", profiles=["dev"])
        class Clock:
            pass

        self.assertIsInstance(registry.get("clock"), Clock)

    def test_register_instance_accepts_profiles(self) -> None:
        registry = Registry(profiles=["dev"])
        dev_db = DevDatabase()
        registry.register_instance(dev_db, name="db", profiles=["dev"])
        registry.register_instance(ProdDatabase(), name="db", profiles=["prod"])
        self.assertIs(registry.get("db"), dev_db)


class ProfileResolutionTestCase(unittest.TestCase):
    def test_inactive_only_name_not_found(self) -> None:
        registry = Registry(profiles=["prod"])
        registry.register(DevDatabase, name="db", profiles=["dev"])
        with self.assertRaises(ServiceNotFoundError):
            registry.get("db")

    def test_inactive_definition_invisible_to_type_lookup(self) -> None:
        registry = Registry()  # no active profiles at all
        registry.register(DevDatabase, profiles=["dev"])
        with self.assertRaises(ServiceNotFoundError):
            registry.get(DevDatabase)
        self.assertNotIn(DevDatabase, registry)

    def test_two_active_matches_are_ambiguous(self) -> None:
        registry = Registry(profiles=["dev", "prod"])
        registry.register(DevDatabase, name="db", profiles=["dev"])
        registry.register(ProdDatabase, name="db", profiles=["prod"])
        with self.assertRaisesRegex(AmbiguousServiceError, "'dev'"):
            registry.get("db")

    def test_injection_picks_active_variant(self) -> None:
        registry = Registry(profiles=["dev"])
        registry.register(DevDatabase, provides=Database, profiles=["dev"])
        registry.register(ProdDatabase, provides=Database, profiles=["prod"])
        registry.register(Consumer)
        self.assertIsInstance(registry.get(Consumer).database, DevDatabase)

    def test_get_all_skips_inactive(self) -> None:
        registry = Registry(profiles=["dev"])
        registry.register(DevDatabase, provides=Database, profiles=["dev"])
        registry.register(ProdDatabase, provides=Database, profiles=["prod"])
        registry.register(Database, name="plain")
        instances = registry.get_all(Database)
        self.assertEqual({type(instance) for instance in instances}, {DevDatabase, Database})

    def test_activity_evaluated_against_owning_layer(self) -> None:
        # the parent owns the definition, so the parent's profiles decide
        parent = Registry(profiles=["prod"])
        parent.register(ProdDatabase, name="db", profiles=["prod"])
        child = Registry(parent=parent, profiles=["dev"])
        self.assertIsInstance(child.get("db"), ProdDatabase)

    def test_child_falls_through_inactive_local_to_parent(self) -> None:
        parent = Registry()
        parent.register(Database, name="db")
        child = Registry(parent=parent, profiles=["prod"])
        child.register(DevDatabase, name="db", profiles=["dev"])
        self.assertIsInstance(child.get("db"), Database)


class ProfileLifecycleTestCase(unittest.TestCase):
    def test_warmup_skips_inactive(self) -> None:
        registry = Registry(profiles=["dev"])
        registry.register(DevDatabase, name="dev", profiles=["dev"], eager=True)
        registry.register(ProdDatabase, name="prod", profiles=["prod"], eager=True)
        instances = registry.warmup()
        self.assertEqual([type(instance) for instance in instances], [DevDatabase])

    def test_awarmup_skips_inactive(self) -> None:
        registry = Registry(profiles=["dev"])
        registry.register(DevDatabase, name="dev", profiles=["dev"], eager=True)
        registry.register(ProdDatabase, name="prod", profiles=["prod"], eager=True)
        instances = asyncio.run(registry.awarmup())
        self.assertEqual([type(instance) for instance in instances], [DevDatabase])

    def test_validate_skips_inactive(self) -> None:
        registry = Registry(profiles=["dev"])
        # unsatisfiable dependency, but inactive — must not be reported
        registry.register(Consumer, name="broken", profiles=["prod"])
        registry.validate()

    def test_validate_reports_active(self) -> None:
        registry = Registry(profiles=["prod"])
        registry.register(Consumer, name="broken", profiles=["prod"])
        with self.assertRaises(ValidationError):
            registry.validate()


class YamlProfilesTestCase(unittest.TestCase):
    def test_profiles_list_and_string(self) -> None:
        registry = Registry(profiles=["prod"])
        registry.load_yaml(
            io.StringIO(
                "dev.mode:\n"
                "  factory: types.SimpleNamespace\n"
                "  profiles: [dev, staging]\n"
                "  mode: dev\n"
                "prod.mode:\n"
                "  factory: types.SimpleNamespace\n"
                "  profiles: prod\n"
                "  mode: prod\n"
            )
        )
        self.assertEqual(registry.get("prod.mode").mode, "prod")
        with self.assertRaises(ServiceNotFoundError):
            registry.get("dev.mode")

    def test_same_name_across_documents_with_disjoint_profiles(self) -> None:
        registry = Registry(profiles=["dev"])
        registry.load_yaml(
            io.StringIO(
                "database:\n  factory: types.SimpleNamespace\n  profiles: dev\n  mode: dev\n"
            )
        )
        registry.load_yaml(
            io.StringIO(
                "database:\n  factory: types.SimpleNamespace\n  profiles: prod\n  mode: prod\n"
            )
        )
        self.assertEqual(registry.get("database").mode, "dev")

    def test_invalid_profiles_value_rejected(self) -> None:
        registry = Registry()
        with self.assertRaisesRegex(DefinitionError, "profiles"):
            registry.load_yaml(
                io.StringIO("database:\n  factory: types.SimpleNamespace\n  profiles: 3\n")
            )


if __name__ == "__main__":
    unittest.main()
