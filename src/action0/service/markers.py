"""Marker objects used to steer injection: :py:class:`Named`, :py:class:`Ref`, ``injected``."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Named:
    """Qualifier for type annotations: inject the service registered under a specific name.

    Use it inside :py:data:`typing.Annotated` when several services provide
    the same type and the parameter needs a particular one::

        def __init__(self, db: Annotated[Database, Named("replica")]) -> None: ...
    """

    name: str
    """The service name to resolve."""


@dataclass(frozen=True)
class Ref:
    """Late-bound reference to another service, usable as a parameter value.

    Put a ``Ref`` into the ``params`` mapping of a registration (or use the
    ``!ref`` tag in YAML) and it is replaced with the referenced service when
    the depending service is built::

        registry.register(Importer, params={"catalog": Ref("gb.geizhals")})

    :param key: the service name, or a type to resolve the default
        implementation for.
    """

    key: "str | type[Any]"
    """The service name or type to resolve when the value is needed."""


class _Injected:
    """Sentinel type for the :py:data:`injected` default value."""

    def __repr__(self) -> str:
        """Return a readable placeholder for error messages and debugging."""
        return "<injected>"


#: Default value marking a parameter to be filled in by
#: :py:meth:`action0.service.registry.Registry.inject`. Typed as ``Any`` so it
#: can be used as the default for a parameter of any annotated type::
#:
#:     @registry.inject
#:     def send_report(report: str, mailer: Mailer = injected) -> None: ...
injected: Any = _Injected()
