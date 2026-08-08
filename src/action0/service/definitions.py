"""Service definitions and provider introspection.

A :py:class:`Definition` is the stored form of one registration: the
provider (class, factory, or a closure over a ready-made instance), what
type it provides, its optional name, scope, and configured parameters.

The module also contains the reflection helpers the registry uses to decide
what can be injected: :py:func:`provider_spec` extracts a provider's
parameters (with resolved type hints), and :py:func:`unwrap_annotation`
normalizes an annotation into its core type plus optionality and an optional
:py:class:`~action0.service.markers.Named` qualifier.
"""

import functools
import inspect
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from typing import Annotated
from typing import Any
from typing import Union

from action0.service.markers import Named

# Value-ish builtin types that are never resolved from the registry by bare
# type annotation: injecting "the registered str" into every `host: str`
# parameter would be a footgun. They can still be configured via `params`,
# defaults, or an explicit Annotated[..., Named("...")] qualifier.
NON_INJECTABLE_TYPES: frozenset[type[Any]] = frozenset(
    {
        object,
        str,
        bytes,
        bytearray,
        int,
        float,
        complex,
        bool,
        list,
        dict,
        set,
        frozenset,
        tuple,
        type(None),
    }
)


@dataclass(eq=False)
class Definition:
    """One registered service: provider, provided type, name, scope, parameters.

    Definitions compare and hash by identity (``eq=False``), so they can be
    used as dictionary keys in scope stores.
    """

    provider: Callable[..., Any]
    """The class or factory callable that produces the service instance."""

    provides: type[Any]
    """The type this service is registered under (used for type lookups)."""

    name: str | None
    """The service name, or ``None`` for an unnamed (default) registration."""

    scope: str
    """Key of the scope policy governing the instance lifetime."""

    params: dict[str, Any] = field(default_factory=dict)
    """Configured constructor parameters (may contain ``Ref`` markers)."""

    default: bool = False
    """Whether this definition wins ambiguous type lookups."""

    eager: bool = False
    """Whether :py:meth:`~action0.service.registry.Registry.warmup` instantiates it."""

    managed: bool = True
    """Whether the registry created the instance and may dispose it on close."""

    introspect: bool = True
    """Whether the provider's signature is inspected for injection."""

    is_async: bool = field(init=False, default=False)
    """Whether the provider is a coroutine function (``async def`` factory).

    Async definitions can only be resolved through the ``a``-prefixed
    registry methods (:py:meth:`~action0.service.registry.Registry.aget`
    and friends); the sync paths refuse them with a clear error.
    """

    def __post_init__(self) -> None:
        """Detect async providers once, at construction time."""
        self.is_async = inspect.iscoroutinefunction(self.provider)

    def label(self) -> str:
        """
        Return a short human-readable identifier for error messages.

        :returns: the provided type's name, plus the service name if set
        """
        base = getattr(self.provides, "__name__", str(self.provides))
        if self.name is not None:
            return f"{base} (name={self.name!r})"
        return base


@dataclass(frozen=True)
class AnonymousFactory:
    """A nested, unregistered definition used as a parameter value.

    Produced by the YAML loader for mappings that contain a ``factory`` key
    inside another service's parameters; the wrapped definition is built
    fresh every time the owning service is built.
    """

    definition: Definition
    """The unregistered definition to build when the parameter is resolved."""


@dataclass(frozen=True)
class ProviderParameter:
    """One injectable parameter of a provider's signature."""

    name: str
    """The parameter name."""

    positional_only: bool
    """Whether the parameter can only be passed positionally."""

    has_default: bool
    """Whether the provider declares a default for this parameter."""

    annotation: Any
    """The resolved type hint, or ``None`` if the parameter has none."""


@dataclass(frozen=True)
class ProviderSpec:
    """The injectable shape of a provider: parameters and ``**kwargs`` presence."""

    parameters: tuple[ProviderParameter, ...]
    """All positional/keyword parameters (``*args``/``**kwargs`` excluded)."""

    has_var_keyword: bool
    """Whether the provider accepts arbitrary keyword arguments."""

    introspectable: bool
    """``False`` when the signature could not be determined (C builtins)."""


def provider_spec(provider: Callable[..., Any]) -> ProviderSpec:
    """
    Return the (cached) :py:class:`ProviderSpec` for a class or factory.

    :param provider: the class or callable to inspect
    :returns: the provider's injectable parameters; for providers whose
        signature cannot be determined, a spec with ``introspectable=False``
    """
    try:
        return _provider_spec_cached(provider)
    except TypeError:
        # unhashable provider (e.g. a callable object defining __eq__ but
        # no __hash__) — compute the spec without caching
        return _build_spec(provider)


@functools.lru_cache(maxsize=1024)
def _provider_spec_cached(provider: Callable[..., Any]) -> ProviderSpec:
    """Cache :py:func:`_build_spec` results per provider."""
    return _build_spec(provider)


def _build_spec(provider: Callable[..., Any]) -> ProviderSpec:
    """
    Inspect ``provider`` and build its :py:class:`ProviderSpec`.

    :param provider: the class or callable to inspect
    :returns: the extracted spec
    """
    try:
        signature = inspect.signature(provider)
    except (TypeError, ValueError):
        # some C-implemented callables expose no signature; configured
        # params are then passed verbatim as keyword arguments
        return ProviderSpec(parameters=(), has_var_keyword=True, introspectable=False)
    hints = _type_hints(provider)
    parameters: list[ProviderParameter] = []
    has_var_keyword = False
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            has_var_keyword = True
            continue
        parameters.append(
            ProviderParameter(
                name=parameter.name,
                positional_only=parameter.kind is inspect.Parameter.POSITIONAL_ONLY,
                has_default=parameter.default is not inspect.Parameter.empty,
                annotation=hints.get(parameter.name),
            )
        )
    return ProviderSpec(
        parameters=tuple(parameters), has_var_keyword=has_var_keyword, introspectable=True
    )


def _type_hints(provider: Callable[..., Any]) -> dict[str, Any]:
    """
    Return ``provider``'s resolved type hints, or ``{}`` if resolution fails.

    :param provider: the class (its ``__init__`` is used) or callable
    :returns: parameter name to resolved annotation, ``Annotated`` preserved
    """
    # mypy flags __init__ access as unsound in general; here any __init__ is
    # exactly the one whose parameter hints we want
    target: Any = provider.__init__ if isinstance(provider, type) else provider  # type: ignore[misc]
    try:
        return typing.get_type_hints(target, include_extras=True)
    except Exception:
        # unresolvable forward references etc. — injection by annotation is
        # unavailable then, but params and defaults still work
        return {}


def infer_provides(provider: Callable[..., Any]) -> type[Any] | None:
    """
    Infer the provided type: the class itself, or a factory's return annotation.

    :param provider: the class or factory callable
    :returns: the provided type, or ``None`` if it cannot be inferred
    """
    if isinstance(provider, type):
        return provider
    return_type = _type_hints(provider).get("return")
    if isinstance(return_type, type) and return_type is not type(None):
        return return_type
    return None


def unwrap_annotation(annotation: Any) -> tuple[Any, bool, str | None]:
    """
    Normalize a type annotation for injection.

    Peels :py:data:`typing.Annotated` layers (collecting a
    :py:class:`~action0.service.markers.Named` qualifier if present) and
    unwraps ``X | None`` / ``Optional[X]`` into ``X`` plus an optional flag.

    :param annotation: the annotation to normalize (may be ``None``)
    :returns: ``(core, optional, named)`` where ``core`` is the remaining
        annotation (``None`` if nothing injectable remains, e.g. for
        multi-type unions), ``optional`` tells whether ``None`` is allowed,
        and ``named`` is the qualifier name if one was attached
    """
    optional = False
    named: str | None = None
    while True:
        if typing.get_origin(annotation) is Annotated:
            annotated_args = typing.get_args(annotation)
            for metadata in annotated_args[1:]:
                if isinstance(metadata, Named):
                    named = metadata.name
            annotation = annotated_args[0]
            continue
        origin = typing.get_origin(annotation)
        if origin is Union or origin is types.UnionType:
            union_args = typing.get_args(annotation)
            non_none = [arg for arg in union_args if arg is not type(None)]
            if len(union_args) == 2 and len(non_none) == 1:
                optional = True
                annotation = non_none[0]
                continue
            # a real multi-type union — not injectable by type
            return None, optional, named
        break
    return annotation, optional, named
