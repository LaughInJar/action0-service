import io
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from action0.service import Definition
from action0.service import DefinitionError
from action0.service import DuplicateServiceError
from action0.service import Registry
from action0.service import ServiceNotFoundError
from action0.service import ValidationError
from action0.service.loader import import_from_path

# The YAML loader imports factories by dotted path, so the test services
# must live in a real importable module: one is generated into a temp
# directory at import time (cleaned up by TemporaryDirectory at exit).
MODULE = "a0svc_loader_mod"

_MODULE_SOURCE = '''
"""Generated services for the loader tests."""


class Database:
    def __init__(self, dsn="sqlite://", timeout="5"):
        self.dsn = dsn
        self.timeout = timeout


class Postgres(Database):
    pass


class Catalog:
    def __init__(self, name, db, predictors=None):
        self.name = name
        self.db = db
        self.predictors = predictors or []


class Predictor:
    def __init__(self, share=0.1):
        self.share = share


class Reserved:
    def __init__(self, scope="none", factory="none"):
        self.scope = scope
        self.factory = factory


def make_database() -> Database:
    return Database("factory://")


def unannotated_factory():
    return Database("unannotated://")
'''

_TMP = tempfile.TemporaryDirectory()
Path(_TMP.name, f"{MODULE}.py").write_text(_MODULE_SOURCE, encoding="utf-8")
sys.path.insert(0, _TMP.name)


class ImportFromPathTestCase(unittest.TestCase):
    """
    tests for :py:func:`action0.service.loader.import_from_path`
    """

    def test_import_module_attribute(self) -> None:
        """
        A dotted path resolves through modules into attributes.
        """
        self.assertIs(import_from_path("io.StringIO"), io.StringIO)
        self.assertIs(import_from_path("os.path.join"), os.path.join)

    def test_import_plain_module(self) -> None:
        """
        A pure module path resolves to the module itself.
        """
        self.assertIs(import_from_path("io"), io)

    def test_invalid_paths_raise(self) -> None:
        """
        Unimportable or malformed paths raise :py:class:`DefinitionError`.
        """
        with self.assertRaises(DefinitionError):
            import_from_path("no.such.module.Anywhere")
        with self.assertRaises(DefinitionError):
            import_from_path("io.NoSuchThing")
        with self.assertRaises(DefinitionError):
            import_from_path("not-a-path!")


class LoadYamlTestCase(unittest.TestCase):
    """
    tests for :py:meth:`action0.service.registry.Registry.load_yaml`
    """

    def load(self, registry: Registry, text: str, **kwargs: Any) -> list[Definition]:
        """
        Load a dedented YAML snippet from a string stream.

        :param registry: the registry to load into
        :param text: the YAML document (indented triple-quoted string)
        :param kwargs: forwarded to ``load_yaml``
        :returns: the registered definitions
        """
        return registry.load_yaml(io.StringIO(textwrap.dedent(text)), **kwargs)

    def test_basic_load(self) -> None:
        """
        Services are registered under their mapping key with flat params.
        """
        registry = Registry()
        self.load(
            registry,
            f"""
            database:
              factory: {MODULE}.Database
              dsn: pg://prod
            """,
        )
        database = registry.get("database")
        self.assertEqual(database.dsn, "pg://prod")
        self.assertIs(registry.get("database"), database)

    def test_load_from_path(self) -> None:
        """
        ``load_yaml`` accepts file paths (str and PathLike).
        """
        registry = Registry()
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = Path(tmp) / "services.yaml"
            yaml_path.write_text(f"database:\n  factory: {MODULE}.Database\n", encoding="utf-8")
            registry.load_yaml(yaml_path)
        self.assertIn("database", registry)

    def test_name_is_a_plain_init_param(self) -> None:
        """
        Unlike the reserved keys, ``name`` flows into the constructor
        (cipopo-style catalogs rely on this).
        """
        registry = Registry()
        self.load(
            registry,
            f"""
            gb.catalog:
              factory: {MODULE}.Catalog
              name: gb
              db:
                factory: {MODULE}.Database
            """,
        )
        self.assertEqual(registry.get("gb.catalog").name, "gb")

    def test_ref_tag(self) -> None:
        """
        ``!ref`` injects another service by name at build time.
        """
        registry = Registry()
        self.load(
            registry,
            f"""
            database:
              factory: {MODULE}.Database

            catalog:
              factory: {MODULE}.Catalog
              name: main
              db: !ref database
            """,
        )
        self.assertIs(registry.get("catalog").db, registry.get("database"))

    def test_dangling_ref_raises_at_build(self) -> None:
        """
        A ``!ref`` to an unknown service raises when the service is built.
        """
        registry = Registry()
        self.load(
            registry,
            f"""
            catalog:
              factory: {MODULE}.Catalog
              name: main
              db: !ref missing
            """,
        )
        with self.assertRaises(ServiceNotFoundError):
            registry.get("catalog")

    def test_env_tag(self) -> None:
        """
        ``!ENV`` substitutes environment variables at load time.
        """
        registry = Registry()
        with mock.patch.dict(os.environ, {"A0SVC_DSN": "pg://env"}):
            self.load(
                registry,
                f"""
                database:
                  factory: {MODULE}.Database
                  dsn: !ENV prefix-${{A0SVC_DSN}}-suffix
                """,
            )
        self.assertEqual(registry.get("database").dsn, "prefix-pg://env-suffix")

    def test_env_tag_fallback(self) -> None:
        """
        ``${VAR:-fallback}`` uses the fallback when the variable is unset.
        """
        registry = Registry()
        environment = {key: value for key, value in os.environ.items() if key != "A0SVC_DSN"}
        with mock.patch.dict(os.environ, environment, clear=True):
            self.load(
                registry,
                f"""
                database:
                  factory: {MODULE}.Database
                  dsn: !ENV ${{A0SVC_DSN:-pg://fallback}}
                """,
            )
        self.assertEqual(registry.get("database").dsn, "pg://fallback")

    def test_env_tag_missing_raises(self) -> None:
        """
        An unset variable without fallback fails at load time.
        """
        registry = Registry()
        environment = {key: value for key, value in os.environ.items() if key != "A0SVC_DSN"}
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(DefinitionError):
                self.load(
                    registry,
                    f"""
                    database:
                      factory: {MODULE}.Database
                      dsn: !ENV ${{A0SVC_DSN}}
                    """,
                )

    def test_nested_anonymous_factories(self) -> None:
        """
        Mappings with a ``factory`` key become fresh anonymous instances,
        also inside lists.
        """
        registry = Registry()
        self.load(
            registry,
            f"""
            catalog:
              factory: {MODULE}.Catalog
              name: main
              db:
                factory: {MODULE}.Database
                dsn: nested://
              predictors:
                - factory: {MODULE}.Predictor
                  share: 0.5
                - factory: {MODULE}.Predictor
            """,
        )
        catalog = registry.get("catalog")
        self.assertEqual(catalog.db.dsn, "nested://")
        self.assertEqual([predictor.share for predictor in catalog.predictors], [0.5, 0.1])

    def test_anchors_merge_and_templates(self) -> None:
        """
        ``.``-prefixed template entries anchor shared config but are not
        registered themselves.
        """
        registry = Registry()
        definitions = self.load(
            registry,
            f"""
            .base: &base
              factory: {MODULE}.Catalog
              db: !ref database

            database:
              factory: {MODULE}.Database

            gb:
              <<: *base
              name: gb

            de:
              <<: *base
              name: de
            """,
        )
        self.assertEqual([d.name for d in definitions], ["database", "gb", "de"])
        self.assertNotIn(".base", registry)
        self.assertEqual(registry.get("gb").name, "gb")
        self.assertIs(registry.get("gb").db, registry.get("de").db)

    def test_scope_key(self) -> None:
        """
        The reserved ``scope`` key controls the instance lifetime.
        """
        registry = Registry()
        self.load(
            registry,
            f"""
            database:
              factory: {MODULE}.Database
              scope: transient
            """,
        )
        self.assertIsNot(registry.get("database"), registry.get("database"))

    def test_provides_key(self) -> None:
        """
        The reserved ``provides`` key widens the registered type.
        """
        registry = Registry()
        self.load(
            registry,
            f"""
            database:
              factory: {MODULE}.Postgres
              provides: {MODULE}.Database
            """,
        )
        base = import_from_path(f"{MODULE}.Database")
        self.assertIsInstance(registry.get(base), import_from_path(f"{MODULE}.Postgres"))

    def test_default_key_disambiguates(self) -> None:
        """
        ``default: true`` wins ambiguous type lookups.
        """
        registry = Registry()
        self.load(
            registry,
            f"""
            first:
              factory: {MODULE}.Database

            second:
              factory: {MODULE}.Database
              default: true
              dsn: pg://second
            """,
        )
        base = import_from_path(f"{MODULE}.Database")
        self.assertEqual(registry.get(base).dsn, "pg://second")

    def test_eager_key_and_warmup(self) -> None:
        """
        ``eager: true`` definitions are built by ``warmup()``.
        """
        registry = Registry()
        self.load(
            registry,
            f"""
            database:
              factory: {MODULE}.Database
              eager: true
            """,
        )
        self.assertEqual(len(registry.warmup()), 1)

    def test_params_escape_hatch(self) -> None:
        """
        Constructor parameters named like reserved keys go under ``params``.
        """
        registry = Registry()
        self.load(
            registry,
            f"""
            reserved:
              factory: {MODULE}.Reserved
              params:
                scope: custom-scope
                factory: custom-factory
            """,
        )
        instance = registry.get("reserved")
        self.assertEqual(instance.scope, "custom-scope")
        self.assertEqual(instance.factory, "custom-factory")

    def test_flat_and_params_clash_raises(self) -> None:
        """
        Giving a parameter both flat and under ``params`` is an error.
        """
        registry = Registry()
        with self.assertRaises(DefinitionError):
            self.load(
                registry,
                f"""
                database:
                  factory: {MODULE}.Database
                  dsn: flat
                  params:
                    dsn: nested
                """,
            )

    def test_factory_function(self) -> None:
        """
        Factories may be functions; the return annotation gives the type.
        """
        registry = Registry()
        self.load(
            registry,
            f"""
            database:
              factory: {MODULE}.make_database
            """,
        )
        self.assertEqual(registry.get("database").dsn, "factory://")

    def test_unannotated_factory_raises_with_context(self) -> None:
        """
        A function factory without return annotation is rejected, naming
        the service.
        """
        registry = Registry()
        with self.assertRaises(DefinitionError) as caught:
            self.load(
                registry,
                f"""
                database:
                  factory: {MODULE}.unannotated_factory
                """,
            )
        self.assertIn("database:", str(caught.exception))

    def test_duplicate_names_raise_unless_replace(self) -> None:
        """
        Re-loading the same names needs ``replace=True``.
        """
        registry = Registry()
        document = f"""
        database:
          factory: {MODULE}.Database
        """
        self.load(registry, document)
        with self.assertRaises(DuplicateServiceError):
            self.load(registry, document)
        self.load(registry, document, replace=True)
        self.assertEqual(len(registry), 1)

    def test_malformed_documents_raise(self) -> None:
        """
        Structural problems are rejected with clear errors.
        """
        registry = Registry()
        with self.assertRaises(DefinitionError):
            self.load(registry, "- just\n- a\n- list\n")
        with self.assertRaises(DefinitionError):
            self.load(registry, "service: just-a-string\n")
        with self.assertRaises(DefinitionError):
            self.load(registry, "service:\n  dsn: no-factory-key\n")
        with self.assertRaises(DefinitionError):
            self.load(registry, "service:\n  factory: no.such.module.Thing\n")

    def test_empty_document_is_fine(self) -> None:
        """
        An empty file registers nothing.
        """
        registry = Registry()
        self.assertEqual(self.load(registry, ""), [])

    def test_validate_after_load(self) -> None:
        """
        A loaded catalog passes static validation.
        """
        registry = Registry()
        self.load(
            registry,
            f"""
            database:
              factory: {MODULE}.Database

            catalog:
              factory: {MODULE}.Catalog
              name: main
              db: !ref database
            """,
        )
        registry.validate()


class LazyLoadTestCase(unittest.TestCase):
    """
    tests for ``Registry.load_yaml(..., lazy=True)``

    Each test generates a module under a fresh, unique name so that
    ``sys.modules`` membership reliably shows whether loading imported it.
    """

    counter = 0

    def fresh_module(self) -> str:
        """
        Generate the standard services module under a new unique name.

        :returns: the generated module's name (not yet imported)
        """
        type(self).counter += 1
        name = f"a0svc_lazy_mod_{self.counter}"
        Path(_TMP.name, f"{name}.py").write_text(_MODULE_SOURCE, encoding="utf-8")
        self.assertNotIn(name, sys.modules)
        return name

    def load(self, registry: Registry, text: str, **kwargs: Any) -> list[Definition]:
        """
        Load a dedented YAML snippet with ``lazy=True``.

        :param registry: the registry to load into
        :param text: the YAML document (indented triple-quoted string)
        :param kwargs: forwarded to ``load_yaml``
        :returns: the registered definitions
        """
        kwargs.setdefault("lazy", True)
        return registry.load_yaml(io.StringIO(textwrap.dedent(text)), **kwargs)

    def test_load_imports_nothing_build_imports(self) -> None:
        """
        Lazy loading leaves the factory module unimported until first
        ``get`` by name; the built service behaves normally afterwards.
        """
        module = self.fresh_module()
        registry = Registry()
        self.load(
            registry,
            f"""
            database:
              factory: {module}.Database
              dsn: pg://lazy
            """,
        )
        self.assertNotIn(module, sys.modules)
        database = registry.get("database")
        self.assertIn(module, sys.modules)
        self.assertEqual(database.dsn, "pg://lazy")
        self.assertIs(registry.get("database"), database)

    def test_type_lookup_materializes(self) -> None:
        """
        A type-based query imports still-lazy definitions first (their
        provided types are needed for the scan).
        """
        module = self.fresh_module()
        registry = Registry()
        self.load(
            registry,
            f"""
            database:
              factory: {module}.Database
            """,
        )
        self.assertNotIn(module, sys.modules)
        found = registry.find(object)
        self.assertIn(module, sys.modules)
        self.assertEqual(type(found).__name__, "Database")

    def test_lazy_provides_path(self) -> None:
        """
        A lazy ``provides`` path resolves on first use like the factory.
        """
        module = self.fresh_module()
        registry = Registry()
        self.load(
            registry,
            f"""
            database:
              factory: {module}.Postgres
              provides: {module}.Database
            """,
        )
        self.assertNotIn(module, sys.modules)
        base = import_from_path(f"{module}.Database")
        self.assertIsInstance(registry.get(base), import_from_path(f"{module}.Postgres"))

    def test_validate_reports_lazy_import_errors(self) -> None:
        """
        ``validate()`` imports lazy definitions and reports every dotted
        path that does not resolve, naming the service.
        """
        module = self.fresh_module()
        registry = Registry()
        self.load(
            registry,
            f"""
            broken:
              factory: no.such.module.Thing

            unannotated:
              factory: {module}.unannotated_factory
            """,
        )
        with self.assertRaises(ValidationError) as caught:
            registry.validate()
        message = str(caught.exception)
        self.assertIn("broken:", message)
        self.assertIn("unannotated:", message)
        # the bad path also raises at first use, naming the service
        with self.assertRaises(DefinitionError) as built:
            registry.get("broken")
        self.assertIn("broken:", str(built.exception))

    def test_validate_materializes_good_paths(self) -> None:
        """
        ``validate()`` on a healthy lazy catalog imports it and passes.
        """
        module = self.fresh_module()
        registry = Registry()
        self.load(
            registry,
            f"""
            database:
              factory: {module}.Database

            catalog:
              factory: {module}.Catalog
              name: main
              db: !ref database
            """,
        )
        self.assertNotIn(module, sys.modules)
        registry.validate()
        self.assertIn(module, sys.modules)

    def test_nested_factories_stay_lazy(self) -> None:
        """
        Anonymous nested factories inside a lazy definition are imported
        only when the owning service is built.
        """
        module = self.fresh_module()
        registry = Registry()
        self.load(
            registry,
            f"""
            catalog:
              factory: {module}.Catalog
              name: main
              db:
                factory: {module}.Database
                dsn: nested://
            """,
        )
        self.assertNotIn(module, sys.modules)
        catalog = registry.get("catalog")
        self.assertEqual(catalog.db.dsn, "nested://")

    def test_replace_and_duplicates_without_import(self) -> None:
        """
        Duplicate handling works on lazy definitions and never triggers
        an import by itself.
        """
        module = self.fresh_module()
        registry = Registry()
        document = f"""
        database:
          factory: {module}.Database
        """
        self.load(registry, document)
        with self.assertRaises(DuplicateServiceError):
            self.load(registry, document)
        self.load(registry, document, replace=True)
        self.assertEqual(len(registry), 1)
        self.assertNotIn(module, sys.modules)
        self.assertEqual(registry.get("database").dsn, "sqlite://")

    def test_eager_lazy_builds_on_warmup(self) -> None:
        """
        ``warmup()`` imports and builds eager lazy definitions.
        """
        module = self.fresh_module()
        registry = Registry()
        self.load(
            registry,
            f"""
            database:
              factory: {module}.Database
              eager: true
            """,
        )
        self.assertNotIn(module, sys.modules)
        instances = registry.warmup()
        self.assertIn(module, sys.modules)
        self.assertEqual(len(instances), 1)
        self.assertIs(registry.get("database"), instances[0])
