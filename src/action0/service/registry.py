"""The service registry: registration, lookup, injection, scopes, lifecycle.

A :py:class:`Registry` maps *types and names* to *service definitions* and
builds instances on demand, resolving missing constructor parameters from
its own registrations:

>>> from action0.service import Registry
>>> class Engine:
...     def __init__(self, url: str = "sqlite://"):
...         self.url = url
>>> class Repository:
...     def __init__(self, engine: Engine):
...         self.engine = engine
>>> registry = Registry()
>>> _ = registry.register(Engine, params={"url": "postgres://db/app"})
>>> _ = registry.register(Repository)
>>> repository = registry.get(Repository)
>>> repository.engine.url
'postgres://db/app'
>>> registry.get(Repository) is repository  # singletons by default
True
"""

import contextlib
import functools
import inspect
import logging
import os
import threading
import typing
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Mapping
from typing import IO
from typing import Any
from typing import ParamSpec
from typing import TypeVar
from typing import cast
from typing import overload

from action0.service.definitions import NON_INJECTABLE_TYPES
from action0.service.definitions import AnonymousFactory
from action0.service.definitions import Definition
from action0.service.definitions import infer_provides
from action0.service.definitions import provider_spec
from action0.service.definitions import unwrap_annotation
from action0.service.errors import AmbiguousServiceError
from action0.service.errors import CircularDependencyError
from action0.service.errors import DefinitionError
from action0.service.errors import DuplicateServiceError
from action0.service.errors import InjectionError
from action0.service.errors import ScopeError
from action0.service.errors import ServiceError
from action0.service.errors import ServiceNotFoundError
from action0.service.errors import ValidationError
from action0.service.markers import Ref
from action0.service.markers import injected
from action0.service.scopes import ContextScope
from action0.service.scopes import Scope
from action0.service.scopes import ScopePolicy
from action0.service.scopes import SingletonScope
from action0.service.scopes import ThreadScope
from action0.service.scopes import TransientScope

log = logging.getLogger(__name__)

# The per-thread stack of definitions currently being built, for cycle
# detection. Module-level (not per registry) so a chain that hops between
# parent and child registries is still recognized as one chain.
_RESOLVING = threading.local()


def _resolution_stack() -> list[Definition]:
    """Return the calling thread's in-progress build stack."""
    stack: list[Definition] | None = getattr(_RESOLVING, "stack", None)
    if stack is None:
        stack = []
        _RESOLVING.stack = stack
    return stack


_T = TypeVar("_T")
_C = TypeVar("_C", bound=Callable[..., Any])
_P = ParamSpec("_P")
_R = TypeVar("_R")


def _describe(annotation: Any) -> str:
    """
    Return a readable name for an annotation in error messages.

    :param annotation: the annotation to describe (may be ``None``)
    :returns: the type name, ``repr`` of the annotation, or a placeholder
    """
    if annotation is None:
        return "<no type annotation>"
    return getattr(annotation, "__name__", None) or repr(annotation)


class Registry:
    """Container that registers service definitions and resolves instances.

    Registrations map a *provider* (class, factory callable, or ready-made
    instance) to the *type it provides*, optionally under a *name*. Lookups
    go by type (subclass-aware) or by name; missing constructor parameters
    are injected from the registry based on type annotations.

    Instances live according to their :py:class:`~action0.service.scopes.Scope`.
    A registry can have a ``parent``: lookups fall back to the parent, local
    registrations shadow it — useful for request-scoped wiring and tests.

    Registries are context managers; leaving the ``with`` block calls
    :py:meth:`close`, which disposes managed instances in reverse creation
    order.
    """

    def __init__(self, *, parent: "Registry | None" = None) -> None:
        """
        Create an empty registry.

        :param parent: optional registry to fall back to when a lookup finds
            nothing locally
        """
        self._parent = parent
        self._definitions: list[Definition] = []
        self._by_name: dict[str, Definition] = {}
        self._overrides: list[Definition] = []
        self._scopes: dict[str, ScopePolicy] = {
            Scope.SINGLETON.value: SingletonScope(),
            Scope.TRANSIENT.value: TransientScope(),
            Scope.THREAD.value: ThreadScope(),
            Scope.CONTEXT.value: ContextScope(),
        }
        self._closed = False

    # ---------------------------------------------------------------- registration

    def register(
        self,
        provider: "type[_T] | Callable[..., _T]",
        *,
        name: str | None = None,
        scope: Scope | str = Scope.SINGLETON,
        params: Mapping[str, Any] | None = None,
        provides: type[Any] | None = None,
        default: bool | None = None,
        eager: bool = False,
        replace: bool = False,
    ) -> Definition:
        """
        Register a class or factory callable as a service.

        :param provider: the class to instantiate, or a factory callable
            whose return annotation tells the provided type
        :param name: optional service name; unnamed registrations are the
            *default implementation* for their type
        :param scope: instance lifetime, one of the built-in
            :py:class:`~action0.service.scopes.Scope` members / their string
            values or a custom scope key (default: singleton)
        :param params: constructor parameters to apply; values may contain
            :py:class:`~action0.service.markers.Ref` markers referencing
            other services
        :param provides: the type to register under; defaults to the class
            itself or the factory's return annotation
        :param default: whether this definition wins ambiguous type lookups;
            defaults to ``True`` for unnamed and ``False`` for named services
        :param eager: instantiate this service in :py:meth:`warmup`
        :param replace: overwrite a colliding registration instead of
            raising :py:class:`~action0.service.errors.DuplicateServiceError`
        :returns: the stored :py:class:`~action0.service.definitions.Definition`
        :raises DefinitionError: if the provider is not callable, the
            provided type cannot be inferred, or ``provides`` does not match
        :raises ScopeError: if ``scope`` names no registered scope
        :raises DuplicateServiceError: on collisions without ``replace``
        """
        self._check_open()
        if not callable(provider):
            raise DefinitionError(f"provider {provider!r} is not callable")
        resolved_provides = provides if provides is not None else infer_provides(provider)
        if resolved_provides is None:
            raise DefinitionError(
                f"cannot infer the provided type of {provider!r}: add a return "
                "annotation to the factory or pass provides=..."
            )
        if provides is not None and isinstance(provider, type):
            if not issubclass(provider, provides):
                raise DefinitionError(
                    f"{provider.__name__} is not a subclass of provides={provides.__name__}"
                )
        definition = Definition(
            provider=provider,
            provides=resolved_provides,
            name=name,
            scope=self._scope_key(scope),
            params=dict(params) if params else {},
            default=default if default is not None else name is None,
            eager=eager,
        )
        self._add(definition, replace=replace)
        return definition

    def register_instance(
        self,
        instance: Any,
        *,
        name: str | None = None,
        provides: type[Any] | None = None,
        default: bool | None = None,
        replace: bool = False,
    ) -> Definition:
        """
        Register an already-built object as a service.

        The instance is served as-is (singleton semantics). Because the
        registry did not create it, :py:meth:`close` will *not* dispose it.

        :param instance: the object to serve
        :param name: optional service name
        :param provides: the type to register under; defaults to
            ``type(instance)``
        :param default: whether this definition wins ambiguous type lookups;
            defaults to ``True`` for unnamed and ``False`` for named services
        :param replace: overwrite a colliding registration instead of raising
        :returns: the stored :py:class:`~action0.service.definitions.Definition`
        :raises DefinitionError: if ``instance`` is not an instance of
            ``provides``
        :raises DuplicateServiceError: on collisions without ``replace``
        """
        self._check_open()
        if provides is not None and not isinstance(instance, provides):
            raise DefinitionError(
                f"{instance!r} is not an instance of provides={provides.__name__}"
            )
        definition = Definition(
            provider=lambda: instance,
            provides=provides if provides is not None else type(instance),
            name=name,
            scope=Scope.SINGLETON.value,
            default=default if default is not None else name is None,
            managed=False,
            introspect=False,
        )
        self._add(definition, replace=replace)
        return definition

    @overload
    def service(self, name: _C, /) -> _C: ...

    @overload
    def service(
        self,
        name: str | None = None,
        /,
        *,
        scope: Scope | str = Scope.SINGLETON,
        params: Mapping[str, Any] | None = None,
        provides: type[Any] | None = None,
        default: bool | None = None,
        eager: bool = False,
        replace: bool = False,
    ) -> Callable[[_C], _C]: ...

    def service(
        self,
        name: Any = None,
        /,
        *,
        scope: Scope | str = Scope.SINGLETON,
        params: Mapping[str, Any] | None = None,
        provides: type[Any] | None = None,
        default: bool | None = None,
        eager: bool = False,
        replace: bool = False,
    ) -> Any:
        """
        Class/factory decorator form of :py:meth:`register`.

        Works bare (``@registry.service``) or with arguments
        (``@registry.service("mailer.bulk", scope=Scope.THREAD)``); the
        decorated class or factory is returned unchanged.

        :param name: the service name, or — in the bare form — the decorated
            class/factory itself
        :param scope: see :py:meth:`register`
        :param params: see :py:meth:`register`
        :param provides: see :py:meth:`register`
        :param default: see :py:meth:`register`
        :param eager: see :py:meth:`register`
        :param replace: see :py:meth:`register`
        :returns: the decorated object, or the decorator to apply
        """
        if callable(name) and not isinstance(name, str):
            # bare @registry.service without parentheses
            self.register(name)
            return name

        def decorate(target: _C) -> _C:
            self.register(
                target,
                name=name,
                scope=scope,
                params=params,
                provides=provides,
                default=default,
                eager=eager,
                replace=replace,
            )
            return target

        return decorate

    def register_scope(self, key: str, policy: ScopePolicy) -> None:
        """
        Register a custom scope under ``key`` (or replace a built-in one).

        :param key: the scope key used in definitions (e.g. ``"request"``)
        :param policy: the :py:class:`~action0.service.scopes.ScopePolicy`
            managing instances for this scope
        """
        self._check_open()
        self._scopes[key] = policy

    def load_yaml(
        self,
        source: "str | os.PathLike[str] | IO[str]",
        *,
        replace: bool = False,
        lazy: bool = False,
    ) -> list[Definition]:
        """
        Load service definitions from a YAML file (requires PyYAML).

        See :py:mod:`action0.service.loader` for the accepted format.

        With ``lazy=True`` the ``factory`` and ``provides`` dotted paths are
        *not* imported at load time. Each definition imports them on first
        use: when the service is built (by-name lookups import nothing
        else), when any type-based lookup consults the registry layer (type
        scans need the real provided types, so they import all still-lazy
        definitions of that layer), and during :py:meth:`validate` and
        :py:meth:`warmup`. Import errors then surface as
        :py:class:`~action0.service.errors.DefinitionError` naming the
        service — call :py:meth:`validate` at boot to collect them early.

        :param source: a file path, or an open text stream (e.g.
            :py:class:`io.StringIO`) containing the YAML document
        :param replace: overwrite colliding registrations instead of raising
        :param lazy: defer importing factory paths until first use
        :returns: the definitions that were registered, in file order
        :raises ServiceError: if PyYAML is not installed
        :raises DefinitionError: if the document is malformed
        """
        self._check_open()
        try:
            from action0.service import loader
        except ModuleNotFoundError as error:
            if error.name != "yaml":
                raise
            raise ServiceError(
                "Registry.load_yaml() requires PyYAML — install 'action0-service[yaml]'"
            ) from error
        registry: Registry = self
        return loader.load(registry, source, replace=replace, lazy=lazy)

    # ---------------------------------------------------------------------- lookup

    @overload
    def get(self, key: type[_T], *, name: str | None = None) -> _T: ...

    @overload
    def get(self, key: str) -> Any: ...

    def get(self, key: "type[_T] | str", *, name: str | None = None) -> Any:
        """
        Return the service instance for a type or name.

        Type lookups are subclass-aware: a service registered as
        ``PostgresDb`` also answers ``get(Database)``. With several
        candidates, the (single) one marked default wins; otherwise an exact
        type match; otherwise :py:class:`~action0.service.errors.AmbiguousServiceError`
        is raised. Lookups not satisfied locally fall back to the parent
        registry.

        :param key: the requested type, or a service name
        :param name: with a type key: request the service with this name and
            verify it provides the requested type
        :returns: the service instance, built and cached per its scope
        :raises ServiceNotFoundError: if nothing matches
        :raises AmbiguousServiceError: if several services match a type
            request and none is clearly the default
        """
        self._check_open()
        definition, owner = self._lookup(key, name)
        return self._resolve(definition, owner)

    @overload
    def find(self, key: type[_T], *, name: str | None = None) -> _T | None: ...

    @overload
    def find(self, key: str) -> Any: ...

    def find(self, key: "type[_T] | str", *, name: str | None = None) -> Any:
        """
        Like :py:meth:`get`, but return ``None`` when nothing matches.

        Ambiguity still raises — an ambiguous request is a configuration
        problem, not an absence.

        :param key: the requested type, or a service name
        :param name: with a type key: request the service with this name
        :returns: the service instance, or ``None`` if nothing is registered
        """
        self._check_open()
        try:
            definition, owner = self._lookup(key, name)
        except ServiceNotFoundError:
            return None
        return self._resolve(definition, owner)

    def get_all(self, key: type[_T]) -> list[_T]:
        """
        Return instances of *all* services providing ``key``.

        Parent registrations come first, then local ones, in registration
        order — handy for plugin-style multi-registrations.

        :param key: the requested type
        :returns: one instance per matching definition (may be empty)
        """
        self._check_open()
        return [
            cast(_T, self._resolve(definition, owner))
            for definition, owner in self._collect_by_type(key)
        ]

    def build(self, provider: "type[_T] | Callable[..., _T]", /, **params: Any) -> _T:
        """
        Construct an instance with injection *without* registering it.

        Useful for one-off objects that want their dependencies wired up:
        missing constructor parameters are resolved exactly as for
        registered services; ``params`` override everything.

        :param provider: the class or factory to call
        :param params: explicit parameter values
        :returns: the new instance (never cached)
        """
        self._check_open()
        provides = provider if isinstance(provider, type) else (infer_provides(provider) or object)
        definition = Definition(
            provider=provider,
            provides=provides,
            name=None,
            scope=Scope.TRANSIENT.value,
            params=params,
            managed=False,
        )
        return cast(_T, self._build_definition(definition))

    def inject(self, func: Callable[_P, _R]) -> Callable[_P, _R]:
        """
        Decorate a function so parameters defaulting to ``injected`` are resolved.

        Only parameters whose default is the
        :py:data:`~action0.service.markers.injected` sentinel (or that are
        explicitly passed as ``injected``) are filled in — the signature
        stays honest for callers and type checkers::

            @registry.inject
            def send_report(report: str, mailer: Mailer = injected) -> None: ...


            send_report("weekly")  # mailer resolved from the registry

        :param func: the function to wrap
        :returns: a wrapper with the same signature
        :raises InjectionError: at call time, if a sentinel parameter cannot
            be resolved
        """
        signature = inspect.signature(func)
        try:
            hints = typing.get_type_hints(func, include_extras=True)
        except Exception:
            hints = {}
        # not every callable is a function object carrying a __qualname__
        label = getattr(func, "__qualname__", None) or repr(func)

        @functools.wraps(func)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            bound = signature.bind_partial(*args, **kwargs)
            for parameter_name, parameter in signature.parameters.items():
                if parameter_name in bound.arguments:
                    if bound.arguments[parameter_name] is not injected:
                        continue
                elif parameter.default is not injected:
                    continue
                # has_default=False: the sentinel is no usable fallback, so
                # resolution failure must raise
                _, value = self._resolve_for_annotation(
                    hints.get(parameter_name),
                    has_default=False,
                    where=f"parameter {parameter_name!r} of {label}()",
                )
                bound.arguments[parameter_name] = value
            return func(*bound.args, **bound.kwargs)

        return wrapper

    @contextlib.contextmanager
    def override(self, key: "type[Any] | str", instance: Any) -> Iterator[Any]:
        """
        Temporarily replace a service with ``instance`` (for tests).

        While the ``with`` block is active, lookups for ``key`` — by type or
        by name, including injections into other services being built —
        return ``instance``. Two caveats: instances cached *before* the
        override (e.g. already-built singletons that had the real service
        injected) are not rewritten, and conversely a singleton first built
        *during* the override keeps the replacement — in tests, prefer a
        fresh registry (or a child registry) per test over overriding in a
        long-lived one. Overrides apply where they are declared: services
        owned by a parent registry build against the parent's world, so
        override on the registry that owns the service.

        :param key: the type or service name to replace
        :param instance: the replacement object
        :returns: a context manager yielding ``instance``
        """
        self._check_open()
        if isinstance(key, str):
            override_name, override_provides = key, type(instance)
        else:
            # deliberately no isinstance check: mocks are the whole point
            override_name, override_provides = None, key
        definition = Definition(
            provider=lambda: instance,
            provides=override_provides,
            name=override_name,
            scope=Scope.TRANSIENT.value,
            default=True,
            managed=False,
            introspect=False,
        )
        self._overrides.append(definition)
        try:
            yield instance
        finally:
            self._overrides.remove(definition)

    # ------------------------------------------------------------------- lifecycle

    def warmup(self) -> list[Any]:
        """
        Instantiate every definition registered with ``eager=True``.

        Call this at application boot to fail fast and to pay construction
        costs up front.

        :returns: the instances that were created (or already cached)
        """
        self._check_open()
        return [
            self._resolve(definition, self)
            for definition in list(self._definitions)
            if definition.eager
        ]

    def validate(self) -> None:
        """
        Statically check every local definition without instantiating anything.

        Detects: unknown ``params`` keys, required parameters that neither
        params, defaults, nor any registration can satisfy, dangling
        :py:class:`~action0.service.markers.Ref` targets, ambiguous
        injections, and dependency cycles.

        :raises ValidationError: listing *all* problems found
        """
        self._check_open()
        problems: list[str] = []
        for definition in self._definitions:
            problems.extend(self._validate_definition(definition))
        problems.extend(self._validate_cycles())
        if problems:
            raise ValidationError(problems)

    def close(self) -> None:
        """
        Dispose managed instances and shut the registry down.

        Every scope hands over the instances it can see from the calling
        thread and context; instances the registry created (not those from
        :py:meth:`register_instance`) get their ``close()`` method called if
        they have one, dependents before dependencies. Errors are logged,
        not raised. After closing, any use of the registry raises
        :py:class:`~action0.service.errors.ServiceError`.
        """
        if self._closed:
            return
        self._closed = True
        for policy in self._scopes.values():
            for definition, instance in policy.drain():
                if not definition.managed:
                    continue
                closer = getattr(instance, "close", None)
                if callable(closer):
                    try:
                        closer()
                    except Exception:
                        log.exception("error closing service %s", definition.label())

    def __enter__(self) -> "Registry":
        """Return ``self``; the registry is usable as a context manager."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the registry when the ``with`` block ends."""
        self.close()

    # -------------------------------------------------------------------- protocol

    def definitions(self) -> tuple[Definition, ...]:
        """
        Return a snapshot of this registry's own definitions (parents excluded).

        :returns: the definitions in registration order
        """
        return tuple(self._definitions)

    def __contains__(self, key: "type[Any] | str") -> bool:
        """Return whether a lookup for ``key`` would find at least one service."""
        if isinstance(key, str):
            return self._find_by_name(key) is not None
        try:
            return self._find_by_type(key) is not None
        except AmbiguousServiceError:
            return True

    def __len__(self) -> int:
        """Return the number of definitions in this registry (parents excluded)."""
        return len(self._definitions)

    def __iter__(self) -> Iterator[Definition]:
        """Iterate over this registry's own definitions."""
        return iter(list(self._definitions))

    def __repr__(self) -> str:
        """Return a compact summary including the parent, if any."""
        parent = f", parent={self._parent!r}" if self._parent is not None else ""
        return f"<Registry: {len(self._definitions)} definitions{parent}>"

    # ------------------------------------------------------------------- internals

    def _check_open(self) -> None:
        """Raise :py:class:`~action0.service.errors.ServiceError` if closed."""
        if self._closed:
            raise ServiceError("this registry has been closed")

    def _scope_key(self, scope: Scope | str) -> str:
        """
        Normalize ``scope`` to its string key and verify it is registered.

        :param scope: a :py:class:`~action0.service.scopes.Scope` member or key
        :returns: the scope key
        :raises ScopeError: if no such scope is registered
        """
        key = scope.value if isinstance(scope, Scope) else str(scope)
        if key not in self._scopes:
            raise ScopeError(f"unknown scope {key!r} (registered: {sorted(self._scopes)})")
        return key

    def _add(self, definition: Definition, *, replace: bool) -> None:
        """
        Store a definition, enforcing name and default-per-type uniqueness.

        :param definition: the definition to store
        :param replace: overwrite collisions instead of raising
        :raises DuplicateServiceError: on collisions without ``replace``
        """
        if definition.name is not None:
            existing = self._by_name.get(definition.name)
            if existing is not None:
                if not replace:
                    raise DuplicateServiceError(
                        f"a service named {definition.name!r} is already registered "
                        f"({existing.label()}); pass replace=True to overwrite"
                    )
                self._remove(existing)
            self._by_name[definition.name] = definition
        else:
            existing = next(
                (
                    candidate
                    for candidate in self._definitions
                    if candidate.name is None and candidate.provides is definition.provides
                ),
                None,
            )
            if existing is not None:
                if not replace:
                    raise DuplicateServiceError(
                        f"an unnamed service providing {existing.label()} is already "
                        "registered; pass replace=True to overwrite, or register by name"
                    )
                self._remove(existing)
        self._definitions.append(definition)

    def _remove(self, definition: Definition) -> None:
        """Remove a stored definition from all indexes."""
        self._definitions.remove(definition)
        if definition.name is not None:
            self._by_name.pop(definition.name, None)

    def _lookup(self, key: "type[Any] | str", name: str | None) -> "tuple[Definition, Registry]":
        """
        Find the definition (and its owning registry) for a get/find request.

        :param key: the requested type or service name
        :param name: optional name qualifier for type requests
        :returns: the matching definition and the registry that owns it
        :raises ServiceNotFoundError: if nothing matches
        :raises AmbiguousServiceError: on ambiguous type requests
        """
        if isinstance(key, str):
            if name is not None:
                raise TypeError("pass name= only when requesting by type")
            found = self._find_by_name(key)
            if found is None:
                raise ServiceNotFoundError(f"no service named {key!r}")
            return found
        if not isinstance(key, type):
            raise TypeError(f"service key must be a type or a name, got {key!r}")
        if name is not None:
            found = self._find_by_name(name)
            if found is None:
                raise ServiceNotFoundError(f"no service named {name!r}")
            definition, _ = found
            if not issubclass(definition.provides, key):
                raise ServiceNotFoundError(
                    f"service {name!r} provides {definition.provides.__name__}, not {key.__name__}"
                )
            return found
        found = self._find_by_type(key)
        if found is None:
            raise ServiceNotFoundError(f"no service registered providing {key.__name__}")
        return found

    def _find_by_name(self, name: str) -> "tuple[Definition, Registry] | None":
        """
        Find a definition by name: overrides first, then own, then parent.

        :param name: the service name
        :returns: the definition and its owning registry, or ``None``
        """
        for definition in reversed(self._overrides):
            if definition.name == name:
                return definition, self
        definition_or_none = self._by_name.get(name)
        if definition_or_none is not None:
            return definition_or_none, self
        if self._parent is not None:
            return self._parent._find_by_name(name)
        return None

    def _find_by_type(self, requested: type[Any]) -> "tuple[Definition, Registry] | None":
        """
        Find the definition for a type request (subclass-aware, layered).

        Each layer (overrides, own definitions, parent) is consulted in turn;
        the first layer with any candidate decides.

        :param requested: the requested type
        :returns: the selected definition and its owner, or ``None``
        :raises AmbiguousServiceError: if a layer has several candidates and
            no clear winner
        """
        # among active overrides the most recent match wins outright
        for definition in reversed(self._overrides):
            if issubclass(definition.provides, requested):
                return definition, self
        # type scans need the real provided types, so lazily-loaded YAML
        # definitions of this layer are imported first (by-name lookups
        # leave them untouched)
        for definition in self._definitions:
            definition.materialize()
        local_candidates = [
            definition
            for definition in self._definitions
            if issubclass(definition.provides, requested)
        ]
        if local_candidates:
            return self._select(local_candidates, requested), self
        if self._parent is not None:
            return self._parent._find_by_type(requested)
        return None

    def _select(self, candidates: list[Definition], requested: type[Any]) -> Definition:
        """
        Pick the winning definition among several type-lookup candidates.

        A single candidate wins; otherwise the single one marked default;
        otherwise the single exact type match (among the defaults, if any).

        :param candidates: the matching definitions (non-empty)
        :param requested: the requested type, for exact-match preference
        :returns: the selected definition
        :raises AmbiguousServiceError: if no rule yields exactly one winner
        """
        if len(candidates) == 1:
            return candidates[0]
        defaults = [definition for definition in candidates if definition.default]
        if len(defaults) == 1:
            return defaults[0]
        pool = defaults or candidates
        exact = [definition for definition in pool if definition.provides is requested]
        if len(exact) == 1:
            return exact[0]
        raise AmbiguousServiceError(
            f"{len(candidates)} services provide {requested.__name__}: "
            + ", ".join(definition.label() for definition in candidates)
            + " — request one by name or mark exactly one with default=True"
        )

    def _collect_by_type(self, requested: type[Any]) -> "list[tuple[Definition, Registry]]":
        """
        Collect all definitions providing ``requested`` across all layers.

        Parents come first; active overrides shadow same-named definitions
        and are appended last.

        :param requested: the requested type
        :returns: ``(definition, owner)`` pairs in resolution order
        """
        collected: list[tuple[Definition, Registry]] = (
            self._parent._collect_by_type(requested) if self._parent is not None else []
        )
        # like _find_by_type: type scans import still-lazy definitions
        for definition in self._definitions:
            definition.materialize()
        collected += [
            (definition, self)
            for definition in self._definitions
            if issubclass(definition.provides, requested)
        ]
        if self._overrides:
            overridden_names = {
                definition.name for definition in self._overrides if definition.name is not None
            }
            collected = [
                (definition, owner)
                for definition, owner in collected
                if definition.name not in overridden_names
            ]
            collected += [
                (definition, self)
                for definition in self._overrides
                if issubclass(definition.provides, requested)
            ]
        return collected

    def _resolve(self, definition: Definition, owner: "Registry") -> Any:
        """
        Return the instance for a definition, honoring its scope.

        Scope state lives on the *owning* registry (a parent's singleton is
        shared by all children). For caching scopes the instance is also
        *built* in the owner's context, so a shared instance can never
        capture a child registry's registrations or overrides; only
        non-caching scopes (transient) resolve their dependencies through
        the requesting registry.

        :param definition: the definition to resolve
        :param owner: the registry the definition is stored in
        :returns: the (new or cached) instance
        :raises ScopeError: if the definition's scope is not registered
        """
        policy = owner._scopes.get(definition.scope)
        if policy is None:
            raise ScopeError(
                f"{definition.label()}: unknown scope {definition.scope!r} "
                f"(registered: {sorted(owner._scopes)})"
            )
        builder = owner if policy.caches else self
        return policy.get(definition, lambda: builder._build_definition(definition))

    def _build_definition(self, definition: Definition) -> Any:
        """
        Actually construct an instance, with cycle detection.

        :param definition: the definition to build
        :returns: the new instance
        :raises CircularDependencyError: if the definition is already being
            built further up the call stack
        """
        stack = _resolution_stack()
        if definition in stack:
            chain = [*stack[stack.index(definition) :], definition]
            raise CircularDependencyError(" -> ".join(link.label() for link in chain))
        stack.append(definition)
        try:
            provider = definition.resolved_provider()
            args, kwargs = self._resolve_arguments(definition, provider)
            return provider(*args, **kwargs)
        finally:
            stack.pop()

    def _resolve_arguments(
        self, definition: Definition, provider: Callable[..., Any]
    ) -> tuple[list[Any], dict[str, Any]]:
        """
        Determine the call arguments for a definition's provider.

        Configured params win; missing parameters are injected by
        annotation; remaining ones fall back to provider defaults.

        :param definition: the definition being built
        :param provider: the definition's (materialized) provider
        :returns: positional arguments and keyword arguments
        :raises DefinitionError: for unknown param keys or unfillable
            positional-only parameters
        :raises InjectionError: for unresolvable required parameters
        """
        if not definition.introspect:
            return [], {}
        spec = provider_spec(provider)
        params = definition.params
        if not spec.introspectable:
            # no signature available: pass configured params verbatim
            return [], {key: self._resolve_value(value) for key, value in params.items()}
        args: list[Any] = []
        kwargs: dict[str, Any] = {}
        skipped_positional = False
        for parameter in spec.parameters:
            if parameter.name in params:
                filled, value = True, self._resolve_value(params[parameter.name])
            else:
                filled, value = self._resolve_for_annotation(
                    parameter.annotation,
                    has_default=parameter.has_default,
                    where=f"parameter {parameter.name!r} of {definition.label()}",
                )
            if not filled:
                if parameter.positional_only:
                    skipped_positional = True
                continue
            if parameter.positional_only:
                if skipped_positional:
                    raise DefinitionError(
                        f"{definition.label()}: cannot fill positional-only parameter "
                        f"{parameter.name!r} because an earlier positional-only "
                        "parameter was left at its default"
                    )
                args.append(value)
            else:
                kwargs[parameter.name] = value
        known = {parameter.name for parameter in spec.parameters}
        unknown = [key for key in params if key not in known]
        if unknown:
            if not spec.has_var_keyword:
                raise DefinitionError(
                    f"{definition.label()}: unknown init parameter(s): "
                    + ", ".join(sorted(unknown))
                )
            for key in unknown:
                kwargs[key] = self._resolve_value(params[key])
        return args, kwargs

    def _resolve_for_annotation(
        self, annotation: Any, *, has_default: bool, where: str
    ) -> tuple[bool, Any]:
        """
        Resolve an injection value from a type annotation.

        Resolution order: a ``Named`` qualifier by name; then the core type
        from the registry (value-ish builtins excluded); then the provider's
        own default; then ``None`` for optional annotations.

        :param annotation: the parameter's annotation (may be ``None``)
        :param has_default: whether the parameter has a provider default to
            fall back to
        :param where: description of the parameter for error messages
        :returns: ``(filled, value)`` — ``filled`` is ``False`` when the
            provider default should be used instead
        :raises InjectionError: if the parameter is required but unresolvable
        :raises AmbiguousServiceError: if several services match the type
        """
        core, optional, named = unwrap_annotation(annotation)
        if named is not None:
            found = self._find_by_name(named)
            if found is not None:
                return True, self._resolve(*found)
            if optional:
                return True, None
            if has_default:
                return False, None
            raise InjectionError(f"cannot resolve {where}: no service named {named!r}")
        if isinstance(core, type) and core not in NON_INJECTABLE_TYPES:
            found = self._find_by_type(core)
            if found is not None:
                return True, self._resolve(*found)
        if has_default:
            return False, None
        if optional:
            return True, None
        raise InjectionError(
            f"cannot resolve {where}: no configured value, no default, and no "
            f"registered service for {_describe(annotation)}"
        )

    def _resolve_value(self, value: Any) -> Any:
        """
        Resolve markers inside a configured parameter value, recursively.

        :param value: the configured value; ``Ref`` markers are looked up,
            :py:class:`~action0.service.definitions.AnonymousFactory` values
            are built fresh, and containers are walked
        :returns: the resolved value
        """
        if isinstance(value, Ref):
            return self._resolve_ref(value)
        if isinstance(value, AnonymousFactory):
            return self._build_definition(value.definition)
        if isinstance(value, list):
            return [self._resolve_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._resolve_value(item) for item in value)
        if isinstance(value, dict):
            return {key: self._resolve_value(item) for key, item in value.items()}
        return value

    def _resolve_ref(self, ref: Ref) -> Any:
        """
        Resolve a :py:class:`~action0.service.markers.Ref` marker.

        :param ref: the reference (service name or type)
        :returns: the referenced service instance
        :raises ServiceNotFoundError: if the target is not registered
        """
        if isinstance(ref.key, str):
            found = self._find_by_name(ref.key)
            if found is None:
                raise ServiceNotFoundError(f"Ref({ref.key!r}): no service with that name")
        else:
            found = self._find_by_type(ref.key)
            if found is None:
                raise ServiceNotFoundError(
                    f"Ref({ref.key.__name__}): no service registered for that type"
                )
        return self._resolve(*found)

    # ------------------------------------------------------------------ validation

    def _validate_definition(self, definition: Definition) -> list[str]:
        """
        Statically check one definition; see :py:meth:`validate`.

        :param definition: the definition to check
        :returns: human-readable problem descriptions (empty when fine)
        """
        problems: list[str] = []
        if not definition.introspect:
            return problems
        try:
            provider = definition.resolved_provider()
        except DefinitionError as error:
            # a lazy factory path that does not import is itself the problem
            return [str(error)]
        spec = provider_spec(provider)
        if not spec.introspectable:
            return problems
        known = {parameter.name for parameter in spec.parameters}
        if not spec.has_var_keyword:
            for key in definition.params:
                if key not in known:
                    problems.append(f"{definition.label()}: unknown init parameter {key!r}")
        for parameter in spec.parameters:
            if parameter.name in definition.params:
                problems.extend(
                    self._validate_value(definition, definition.params[parameter.name])
                )
                continue
            where = f"{definition.label()}: parameter {parameter.name!r}"
            core, optional, named = unwrap_annotation(parameter.annotation)
            if named is not None:
                if self._find_by_name(named) is None and not (optional or parameter.has_default):
                    problems.append(f"{where} references unknown service name {named!r}")
                continue
            if isinstance(core, type) and core not in NON_INJECTABLE_TYPES:
                try:
                    if self._find_by_type(core) is not None:
                        continue
                except AmbiguousServiceError as error:
                    problems.append(f"{where}: {error}")
                    continue
            if parameter.has_default or optional:
                continue
            problems.append(
                f"{where} cannot be resolved: no configured value, no default, and no "
                f"registered service for {_describe(parameter.annotation)}"
            )
        return problems

    def _validate_value(self, definition: Definition, value: Any) -> list[str]:
        """
        Statically check one configured parameter value, recursively.

        :param definition: the definition owning the value (for messages)
        :param value: the configured value to walk
        :returns: problem descriptions for dangling refs and nested factories
        """
        problems: list[str] = []
        if isinstance(value, Ref):
            try:
                found = (
                    self._find_by_name(value.key)
                    if isinstance(value.key, str)
                    else self._find_by_type(value.key)
                )
            except AmbiguousServiceError as error:
                problems.append(f"{definition.label()}: {error}")
                return problems
            if found is None:
                problems.append(
                    f"{definition.label()}: Ref({value.key!r}) does not match any service"
                )
        elif isinstance(value, AnonymousFactory):
            problems.extend(self._validate_definition(value.definition))
        elif isinstance(value, (list, tuple)):
            for item in value:
                problems.extend(self._validate_value(definition, item))
        elif isinstance(value, dict):
            for item in value.values():
                problems.extend(self._validate_value(definition, item))
        return problems

    def _injection_targets(self, definition: Definition) -> list[Definition]:
        """
        Compute which definitions ``definition`` would pull in when built.

        Used by the static cycle check; anonymous nested factories are
        transparent (their dependencies count as the owner's).

        :param definition: the definition to analyze
        :returns: the definitions it depends on
        """
        targets: list[Definition] = []

        def from_value(value: Any) -> None:
            if isinstance(value, Ref):
                try:
                    found = (
                        self._find_by_name(value.key)
                        if isinstance(value.key, str)
                        else self._find_by_type(value.key)
                    )
                except AmbiguousServiceError:
                    found = None
                if found is not None:
                    targets.append(found[0])
            elif isinstance(value, AnonymousFactory):
                collect(value.definition)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    from_value(item)
            elif isinstance(value, dict):
                for item in value.values():
                    from_value(item)

        def collect(current: Definition) -> None:
            if not current.introspect:
                return
            try:
                provider = current.resolved_provider()
            except DefinitionError:
                # unimportable lazy paths are reported by _validate_definition
                return
            spec = provider_spec(provider)
            if not spec.introspectable:
                for value in current.params.values():
                    from_value(value)
                return
            for parameter in spec.parameters:
                if parameter.name in current.params:
                    from_value(current.params[parameter.name])
                    continue
                core, _, named = unwrap_annotation(parameter.annotation)
                if named is not None:
                    found = self._find_by_name(named)
                    if found is not None:
                        targets.append(found[0])
                    continue
                if isinstance(core, type) and core not in NON_INJECTABLE_TYPES:
                    try:
                        found = self._find_by_type(core)
                    except AmbiguousServiceError:
                        found = None
                    if found is not None:
                        targets.append(found[0])

        collect(definition)
        return targets

    def _validate_cycles(self) -> list[str]:
        """
        Detect dependency cycles among the local definitions statically.

        :returns: one problem description per distinct cycle found
        """
        problems: list[str] = []
        reported: set[frozenset[int]] = set()
        done: set[Definition] = set()
        path: list[Definition] = []
        on_path: set[Definition] = set()

        def visit(definition: Definition) -> None:
            if definition in done:
                return
            if definition in on_path:
                cycle = path[path.index(definition) :] + [definition]
                key = frozenset(id(link) for link in cycle)
                if key not in reported:
                    reported.add(key)
                    problems.append(
                        "dependency cycle: " + " -> ".join(link.label() for link in cycle)
                    )
                return
            on_path.add(definition)
            path.append(definition)
            for target in self._injection_targets(definition):
                visit(target)
            path.pop()
            on_path.discard(definition)
            done.add(definition)

        for definition in self._definitions:
            visit(definition)
        return problems
