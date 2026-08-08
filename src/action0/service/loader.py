"""Load service definitions from YAML files (requires PyYAML).

The document is a mapping of service names to definitions. Within a
definition, a handful of *reserved keys* configure the registration; every
other key is a constructor parameter:

.. code-block:: yaml

    shop.catalog:
      factory: myapp.catalog.ApiCatalog   # dotted path — required
      scope: singleton                    # optional (default: singleton)
      provides: myapp.catalog.Catalog     # optional, defaults to the class
      default: true                       # optional: wins type lookups
      eager: false                        # optional: built by warmup()
      profiles: [prod]                    # optional: active only under these
                                          # profiles (string or list)
      api_key: !ENV ${CATALOG_KEY}        # everything else: init params
      db: !ref database                   # inject another service by name
      retry_policy:                       # nested mapping with "factory":
        factory: myapp.util.Retry         # built fresh as an anonymous object
        attempts: 3

Supported YAML conveniences:

- ``!ENV`` substitutes ``${VAR}`` / ``${VAR:-fallback}`` from the process
  environment inside a scalar, at load time.
- ``!ref name`` injects the service registered under ``name`` at build time.
- Standard YAML anchors and merge keys (``&base`` / ``<<: *base``) work as
  usual for sharing configuration between definitions. Entries whose key
  starts with a ``.`` are *templates*: they are parsed (so their anchors can
  be referenced) but not registered.
- ``!ENV`` substitution is purely textual — values arrive as strings, they
  are *not* re-parsed as YAML (so a secret like ``"yes"`` or ``"0123"``
  cannot change type behind your back).
- If a constructor parameter is itself named like a reserved key, put it
  under the ``params:`` mapping, which is passed through verbatim.
- With ``lazy=True`` the ``factory``/``provides`` dotted paths (including
  those of nested anonymous factories) are not imported at load time but on
  first use — see
  :py:meth:`~action0.service.registry.Registry.load_yaml`.

Parsing uses a :py:class:`yaml.SafeLoader` subclass, so documents cannot
instantiate arbitrary Python objects *during parsing* — but ``factory``
paths are imported and called, so only load files you trust.
"""

import importlib
import os
import re
from pathlib import Path
from typing import IO
from typing import TYPE_CHECKING
from typing import Any

import yaml

from action0.service.definitions import AnonymousFactory
from action0.service.definitions import Definition
from action0.service.errors import DefinitionError
from action0.service.errors import DuplicateServiceError
from action0.service.markers import Ref
from action0.service.scopes import Scope

if TYPE_CHECKING:  # imported lazily by Registry.load_yaml, avoid the cycle
    from action0.service.registry import Registry

# keys of a YAML definition that configure the registration itself;
# everything else is a constructor parameter
RESERVED_KEYS = frozenset(
    {"factory", "scope", "provides", "default", "eager", "profiles", "params"}
)

# inside anonymous nested factories only these are reserved
_NESTED_RESERVED_KEYS = frozenset({"factory", "params"})

# ${VAR} or ${VAR:-fallback}, substituted by the !ENV tag
_ENV_PATTERN = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<fallback>[^}]*))?\}")


def _substitute_env(value: str) -> str:
    """
    Replace ``${VAR}`` / ``${VAR:-fallback}`` occurrences from the environment.

    :param value: the scalar tagged with ``!ENV``
    :returns: the substituted string
    :raises DefinitionError: if a variable without fallback is not set
    """

    def replace(match: "re.Match[str]") -> str:
        name = match.group("name")
        fallback = match.group("fallback")
        env_value = os.environ.get(name)
        if env_value is None:
            if fallback is not None:
                return fallback
            raise DefinitionError(
                f"environment variable {name!r} is not set (referenced via !ENV)"
            )
        return env_value

    return _ENV_PATTERN.sub(replace, value)


class _ConfigLoader(yaml.SafeLoader):
    """SafeLoader with the ``!ENV`` and ``!ref`` convenience tags."""


def _env_constructor(loader: yaml.SafeLoader, node: yaml.Node) -> str:
    """Construct an ``!ENV``-tagged scalar by substituting environment variables."""
    if not isinstance(node, yaml.ScalarNode):
        raise DefinitionError("!ENV only applies to scalar values")
    return _substitute_env(loader.construct_scalar(node))


def _ref_constructor(loader: yaml.SafeLoader, node: yaml.Node) -> Ref:
    """Construct a ``!ref``-tagged scalar into a service reference."""
    if not isinstance(node, yaml.ScalarNode):
        raise DefinitionError("!ref only applies to scalar values")
    return Ref(loader.construct_scalar(node))


_ConfigLoader.add_constructor("!ENV", _env_constructor)
_ConfigLoader.add_constructor("!ref", _ref_constructor)


def import_from_path(path: str) -> Any:
    """
    Import an object from a dotted path like ``myapp.mail.SmtpMailer``.

    The longest importable module prefix is imported, the remaining segments
    are resolved with ``getattr`` (so nested classes work too).

    :param path: the dotted path
    :returns: the imported object
    :raises DefinitionError: if the path cannot be resolved
    """
    parts = path.split(".")
    if not all(part.isidentifier() for part in parts):
        raise DefinitionError(f"{path!r} is not a valid dotted path")
    module = None
    module_error: Exception | None = None
    split = len(parts)
    while split > 0:
        try:
            module = importlib.import_module(".".join(parts[:split]))
        except ImportError as error:
            module_error = error
            split -= 1
            continue
        break
    if module is None:
        raise DefinitionError(f"cannot import {path!r}: {module_error}")
    target: Any = module
    for attribute in parts[split:]:
        try:
            target = getattr(target, attribute)
        except AttributeError as error:
            raise DefinitionError(f"cannot import {path!r}: {error}") from error
    return target


def load(
    registry: "Registry",
    source: "str | os.PathLike[str] | IO[str]",
    *,
    replace: bool = False,
    lazy: bool = False,
) -> list[Definition]:
    """
    Parse a YAML document and register every service it defines.

    :param registry: the registry to register into
    :param source: a file path, or an open text stream containing YAML
    :param replace: overwrite colliding registrations instead of raising
    :param lazy: defer importing ``factory``/``provides`` paths until the
        definitions are first used
    :returns: the registered definitions, in document order
    :raises DefinitionError: if the document or a definition is malformed
    """
    if isinstance(source, (str, os.PathLike)):
        text = Path(source).read_text(encoding="utf-8")
    else:
        text = source.read()
    document = yaml.load(text, Loader=_ConfigLoader)
    if document is None:
        return []
    if not isinstance(document, dict):
        raise DefinitionError("the YAML top level must be a mapping of service names")
    created: list[Definition] = []
    for key, node in document.items():
        if not isinstance(key, str):
            raise DefinitionError(f"service name {key!r} is not a string")
        if key.startswith("."):
            # template entry: only there to be referenced via YAML anchors
            continue
        if not isinstance(node, dict):
            raise DefinitionError(f"{key}: the definition must be a mapping")
        created.append(_register_one(registry, key, node, replace=replace, lazy=lazy))
    return created


def _register_one(
    registry: "Registry", key: str, node: dict[Any, Any], *, replace: bool, lazy: bool
) -> Definition:
    """
    Register a single YAML definition.

    :param registry: the registry to register into
    :param key: the service name (the definition's mapping key)
    :param node: the parsed definition mapping
    :param replace: overwrite colliding registrations instead of raising
    :param lazy: keep the dotted paths unimported for now
    :returns: the registered definition
    :raises DefinitionError: if the definition is malformed
    """
    spec = dict(node)
    factory_path = spec.pop("factory", None)
    if not isinstance(factory_path, str):
        raise DefinitionError(f"{key}: 'factory' (a dotted path string) is required")
    provider = None if lazy else _import_factory(key, factory_path)
    scope = spec.pop("scope", Scope.SINGLETON.value)
    if not isinstance(scope, str):
        raise DefinitionError(f"{key}: 'scope' must be a string")
    provides_path = spec.pop("provides", None)
    provides: type[Any] | None = None
    if provides_path is not None:
        if not isinstance(provides_path, str):
            raise DefinitionError(f"{key}: 'provides' must be a dotted path string")
        if not lazy:
            provides_object = _import_factory(key, provides_path)
            if not isinstance(provides_object, type):
                raise DefinitionError(f"{key}: 'provides' must point to a class")
            provides = provides_object
    default = bool(spec.pop("default", False))
    eager = bool(spec.pop("eager", False))
    profiles = _pop_profiles(key, spec)
    params = _pop_params(key, spec, lazy=lazy)
    if lazy:
        # register the unimported dotted paths; Definition.materialize()
        # resolves them on first use. _scope_key/_add are the registration
        # internals behind Registry.register() — used directly because
        # there is no provider to pass yet.
        definition = Definition(
            provider=None,
            provides=object,
            name=key,
            scope=registry._scope_key(scope),
            params=params,
            default=default,
            eager=eager,
            profiles=frozenset(profiles or ()),
            factory_path=factory_path,
            provides_path=provides_path,
        )
        registry._add(definition, replace=replace)
        return definition
    assert provider is not None  # lazy returned above; non-lazy imported it
    try:
        return registry.register(
            provider,
            name=key,
            scope=scope,
            params=params,
            provides=provides,
            default=default,
            eager=eager,
            profiles=profiles,
            replace=replace,
        )
    except DuplicateServiceError:
        # the message already names the colliding service
        raise
    except DefinitionError as error:
        raise DefinitionError(f"{key}: {error}") from error


def _pop_profiles(key: str, spec: dict[Any, Any]) -> list[str] | None:
    """
    Extract and check the optional ``profiles`` key of a definition.

    :param key: the service name, for error messages
    :param spec: the definition mapping being consumed
    :returns: the profile names, or ``None`` when the key is absent
    :raises DefinitionError: if the value is not a string or list of strings
    """
    value = spec.pop("profiles", None)
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise DefinitionError(f"{key}: 'profiles' must be a string or a list of strings")


def _pop_params(key: str, spec: dict[Any, Any], *, lazy: bool) -> dict[str, Any]:
    """
    Combine flat parameter keys with an explicit ``params:`` mapping.

    The explicit mapping is *not* scanned for reserved keys — it is the
    escape hatch for constructor parameters named like one.

    :param key: the service name, for error messages
    :param spec: the definition mapping with reserved keys already removed
    :param lazy: keep nested factory paths unimported for now
    :returns: the transformed parameter mapping
    :raises DefinitionError: if ``params`` is not a mapping or keys clash
    """
    explicit = spec.pop("params", None)
    if explicit is not None and not isinstance(explicit, dict):
        raise DefinitionError(f"{key}: 'params' must be a mapping")
    params: dict[str, Any] = {}
    for param_key, value in spec.items():
        if not isinstance(param_key, str):
            raise DefinitionError(f"{key}: parameter name {param_key!r} is not a string")
        params[param_key] = _transform(key, value, lazy=lazy)
    if explicit:
        for param_key, value in explicit.items():
            if not isinstance(param_key, str):
                raise DefinitionError(f"{key}: parameter name {param_key!r} is not a string")
            if param_key in params:
                raise DefinitionError(
                    f"{key}: parameter {param_key!r} is given both flat and under 'params'"
                )
            params[param_key] = _transform(key, value, lazy=lazy)
    return params


def _transform(key: str, value: Any, *, lazy: bool) -> Any:
    """
    Recursively prepare a parameter value from the parsed YAML.

    Mappings containing a ``factory`` key become
    :py:class:`~action0.service.definitions.AnonymousFactory` values (built
    fresh whenever the owning service is built); containers are walked.

    :param key: the service name, for error messages
    :param value: the parsed YAML value
    :param lazy: keep nested factory paths unimported for now
    :returns: the transformed value
    :raises DefinitionError: if a nested factory is malformed
    """
    if isinstance(value, dict):
        if "factory" in value:
            return _anonymous_factory(key, value, lazy=lazy)
        return {item_key: _transform(key, item, lazy=lazy) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_transform(key, item, lazy=lazy) for item in value]
    return value


def _anonymous_factory(key: str, node: dict[Any, Any], *, lazy: bool) -> AnonymousFactory:
    """
    Turn a nested ``factory:`` mapping into an anonymous definition.

    Inside nested factories only ``factory`` and ``params`` are reserved;
    everything else is a constructor parameter. With ``lazy=True`` the
    nested path is imported when the owning service is first built.

    :param key: the owning service name, for error messages
    :param node: the nested mapping
    :param lazy: keep the nested factory path unimported for now
    :returns: the wrapped, unregistered definition
    :raises DefinitionError: if the nested factory is malformed
    """
    spec = dict(node)
    factory_path = spec.pop("factory")
    if not isinstance(factory_path, str):
        raise DefinitionError(f"{key}: nested 'factory' must be a dotted path string")
    provider = None if lazy else _import_factory(key, factory_path)
    explicit = spec.pop("params", None)
    if explicit is not None and not isinstance(explicit, dict):
        raise DefinitionError(f"{key}: nested 'params' must be a mapping")
    params: dict[str, Any] = {}
    for param_key, value in spec.items():
        if not isinstance(param_key, str):
            raise DefinitionError(f"{key}: parameter name {param_key!r} is not a string")
        params[param_key] = _transform(key, value, lazy=lazy)
    if explicit:
        for param_key, value in explicit.items():
            params[param_key] = _transform(key, value, lazy=lazy)
    provides = provider if isinstance(provider, type) else object
    definition = Definition(
        provider=provider,
        provides=provides,
        name=None,
        scope=Scope.TRANSIENT.value,
        params=params,
        factory_path=factory_path if lazy else None,
    )
    return AnonymousFactory(definition)


def _import_factory(key: str, path: str) -> Any:
    """
    Import a dotted path, wrapping errors with the service name for context.

    :param key: the service name, for error messages
    :param path: the dotted path to import
    :returns: the imported object
    :raises DefinitionError: if the import fails
    """
    try:
        return import_from_path(path)
    except DefinitionError as error:
        raise DefinitionError(f"{key}: {error}") from error
