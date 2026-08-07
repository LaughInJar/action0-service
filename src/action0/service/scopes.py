"""Service scopes: how long a built instance is kept and who shares it.

The built-in scopes are addressed through the :py:class:`Scope` enum (or
their string values, e.g. in YAML files):

- ``singleton`` — one instance per registry, shared by everyone (the default)
- ``transient`` — a fresh instance for every request
- ``thread`` — one instance per thread
- ``context`` — one instance per :py:mod:`contextvars` context (which makes
  it task-local under :py:mod:`asyncio`)

Custom scopes are plain :py:class:`ScopePolicy` subclasses registered with
:py:meth:`action0.service.registry.Registry.register_scope`.
"""

import contextvars
import enum
import threading
from abc import ABC
from abc import abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:  # avoid a runtime import cycle; only needed for annotations
    from action0.service.definitions import Definition

# A single, process-wide creation lock: instance creation is rare, and one
# shared lock cannot deadlock no matter how registries and scopes call into
# each other (it is re-entrant for nested dependency builds).
_CREATION_LOCK = threading.RLock()

# sentinel for "no instance stored yet" in context variables
_MISSING: Any = object()


class Scope(enum.Enum):
    """Names of the built-in scopes.

    Everywhere a scope is expected, the enum member and its string value
    (``"singleton"``, ``"transient"``, ``"thread"``, ``"context"``) are
    interchangeable; custom scopes are addressed by their registered string.
    """

    SINGLETON = "singleton"
    TRANSIENT = "transient"
    THREAD = "thread"
    CONTEXT = "context"


class ScopePolicy(ABC):
    """Strategy deciding whether to reuse a stored instance or build a new one.

    A policy instance belongs to exactly one
    :py:class:`~action0.service.registry.Registry` and holds the instances
    for every definition using its scope.
    """

    caches: bool = True
    """Whether instances are stored and shared.

    Caching scopes build their instances in the context of the registry that
    *owns* the definition, so a shared instance can never capture
    registrations of the (possibly short-lived) registry that happened to
    request it first. Set to ``False`` for scopes that build fresh instances
    every time; those resolve dependencies through the requesting registry.
    """

    @abstractmethod
    def get(self, definition: "Definition", build: Callable[[], Any]) -> Any:
        """
        Return the instance for ``definition``, building it if necessary.

        :param definition: the definition being resolved (usable as a dict
            key; definitions hash by identity)
        :param build: zero-argument callable producing a new instance
        :returns: the (new or cached) service instance
        """

    def drain(self) -> list[tuple["Definition", Any]]:
        """
        Hand over all stored instances *visible from the calling thread and
        context* for disposal and forget them.

        :returns: ``(definition, instance)`` pairs, in reverse creation order
            (dependents before their dependencies)
        """
        return []


class SingletonScope(ScopePolicy):
    """One shared instance per registry."""

    def __init__(self) -> None:
        """Set up the (insertion-ordered) instance store."""
        self._instances: dict[Definition, Any] = {}

    def get(self, definition: "Definition", build: Callable[[], Any]) -> Any:
        """Return the stored instance, building it under the creation lock once."""
        try:
            return self._instances[definition]
        except KeyError:
            pass
        with _CREATION_LOCK:
            # double-checked: another thread may have built it while we waited
            if definition not in self._instances:
                self._instances[definition] = build()
            return self._instances[definition]

    def drain(self) -> list[tuple["Definition", Any]]:
        """Return all singletons in reverse creation order and clear the store."""
        with _CREATION_LOCK:
            drained = list(self._instances.items())
            self._instances.clear()
        return list(reversed(drained))


class TransientScope(ScopePolicy):
    """A fresh instance on every request; nothing is stored (or disposed)."""

    caches = False

    def get(self, definition: "Definition", build: Callable[[], Any]) -> Any:
        """Build a new instance every time."""
        return build()


class ThreadScope(ScopePolicy):
    """One instance per thread (backed by :py:class:`threading.local`)."""

    def __init__(self) -> None:
        """Set up the per-thread instance store."""
        self._local = threading.local()

    def _store(self) -> "dict[Definition, Any]":
        """Return the calling thread's instance store, creating it on first use."""
        store: dict[Definition, Any] | None = getattr(self._local, "store", None)
        if store is None:
            store = {}
            self._local.store = store
        return store

    def get(self, definition: "Definition", build: Callable[[], Any]) -> Any:
        """Return the calling thread's instance, building it on first use."""
        store = self._store()
        # no lock needed: the store is only ever touched by its own thread
        if definition not in store:
            store[definition] = build()
        return store[definition]

    def drain(self) -> list[tuple["Definition", Any]]:
        """Return the *calling thread's* instances (other threads' survive)."""
        store = self._store()
        drained = list(store.items())
        store.clear()
        return list(reversed(drained))


class ContextScope(ScopePolicy):
    """One instance per :py:mod:`contextvars` context (task-local in asyncio)."""

    def __init__(self) -> None:
        """Set up the per-definition context variables."""
        self._vars: dict[Definition, contextvars.ContextVar[Any]] = {}
        self._order: list[Definition] = []

    def _var(self, definition: "Definition") -> contextvars.ContextVar[Any]:
        """Return the context variable holding ``definition``'s instances."""
        with _CREATION_LOCK:
            var = self._vars.get(definition)
            if var is None:
                var = contextvars.ContextVar(f"action0.service:{definition.label()}")
                self._vars[definition] = var
                self._order.append(definition)
            return var

    def get(self, definition: "Definition", build: Callable[[], Any]) -> Any:
        """Return the current context's instance, building it on first use."""
        var = self._var(definition)
        instance = var.get(_MISSING)
        if instance is _MISSING:
            instance = build()
            var.set(instance)
        return instance

    def drain(self) -> list[tuple["Definition", Any]]:
        """Return the instances visible in the *current* context and unset them."""
        drained: list[tuple[Definition, Any]] = []
        with _CREATION_LOCK:
            pairs = [(definition, self._vars[definition]) for definition in self._order]
        for definition, var in pairs:
            instance = var.get(_MISSING)
            if instance is not _MISSING:
                drained.append((definition, instance))
                var.set(_MISSING)
        return list(reversed(drained))
