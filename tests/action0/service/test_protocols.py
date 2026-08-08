import unittest
from typing import Protocol
from typing import runtime_checkable

from action0.service import AmbiguousServiceError
from action0.service import DefinitionError
from action0.service import Registry
from action0.service import ServiceError
from action0.service import ServiceNotFoundError
from action0.service import injected


@runtime_checkable
class Speaker(Protocol):
    """A structural interface: anything with a ``speak()`` method."""

    def speak(self) -> str:
        """Return an utterance."""
        ...


class Quiet(Protocol):
    """A protocol *without* ``@runtime_checkable``, for the error tests."""

    def speak(self) -> str:
        """Return an utterance."""
        ...


@runtime_checkable
class HasDsn(Protocol):
    """A protocol with a data member — ``issubclass`` refuses these."""

    dsn: str


class Dog:
    """Satisfies :py:class:`Speaker` structurally, without inheriting it."""

    def speak(self) -> str:
        """Return an utterance."""
        return "woof"


class Cat:
    """A second structural :py:class:`Speaker` implementation."""

    def speak(self) -> str:
        """Return an utterance."""
        return "meow"


class Anvil:
    """Satisfies no protocol used in these tests."""


class Config:
    """Satisfies :py:class:`HasDsn` structurally (data member only)."""

    def __init__(self) -> None:
        """Set the data member the protocol requires."""
        self.dsn = "sqlite://"


class Announcer:
    """A service depending on a protocol-typed parameter."""

    def __init__(self, speaker: Speaker) -> None:
        """Store the injected speaker."""
        self.speaker = speaker


class ProtocolLookupTestCase(unittest.TestCase):
    """
    structural type lookups with runtime-checkable protocols
    """

    def test_get_by_protocol(self) -> None:
        """
        A registration whose type satisfies the protocol answers get(Protocol).
        """
        registry = Registry()
        registry.register(Dog)
        speaker = registry.get(Speaker)  # type: ignore[type-abstract]
        self.assertIsInstance(speaker, Dog)
        self.assertEqual(speaker.speak(), "woof")

    def test_get_by_protocol_nothing_matches(self) -> None:
        """
        No structural match raises ServiceNotFoundError, like any type lookup.
        """
        registry = Registry()
        registry.register(Anvil)
        with self.assertRaises(ServiceNotFoundError):
            registry.get(Speaker)  # type: ignore[type-abstract]

    def test_get_all_by_protocol(self) -> None:
        """
        get_all collects every structural match, in registration order.
        """
        registry = Registry()
        registry.register(Dog, name="dog")
        registry.register(Cat, name="cat")
        registry.register(Anvil)
        speakers = registry.get_all(Speaker)  # type: ignore[type-abstract]
        self.assertEqual([type(s) for s in speakers], [Dog, Cat])

    def test_protocol_ambiguity_raises(self) -> None:
        """
        Two default registrations matching one protocol are ambiguous.
        """
        registry = Registry()
        registry.register(Dog)
        registry.register(Cat)
        with self.assertRaises(AmbiguousServiceError):
            registry.get(Speaker)  # type: ignore[type-abstract]

    def test_typed_and_named_protocol_lookup(self) -> None:
        """
        get(Protocol, name=...) verifies the named service structurally.
        """
        registry = Registry()
        registry.register(Dog, name="dog")
        registry.register(Anvil, name="anvil")
        self.assertIsInstance(registry.get(Speaker, name="dog"), Dog)  # type: ignore[type-abstract]
        with self.assertRaises(ServiceNotFoundError):
            registry.get(Speaker, name="anvil")  # type: ignore[type-abstract]

    def test_nominal_lookup_unchanged(self) -> None:
        """
        Plain class lookups keep their nominal subclass semantics.
        """

        class Base:
            pass

        class Impl(Base):
            pass

        registry = Registry()
        registry.register(Impl)
        self.assertIsInstance(registry.get(Base), Impl)
        with self.assertRaises(ServiceNotFoundError):
            registry.get(Anvil)


class ProtocolInjectionTestCase(unittest.TestCase):
    """
    constructor and function injection through protocol annotations
    """

    def test_constructor_injection_via_protocol(self) -> None:
        """
        A protocol-annotated constructor parameter resolves structurally.
        """
        registry = Registry()
        registry.register(Dog)
        registry.register(Announcer)
        announcer = registry.get(Announcer)
        self.assertIsInstance(announcer.speaker, Dog)

    def test_function_injection_via_protocol(self) -> None:
        """
        @registry.inject resolves protocol-annotated sentinel parameters.
        """
        registry = Registry()
        registry.register(Cat)

        @registry.inject
        def announce(speaker: Speaker = injected) -> str:
            return speaker.speak()

        self.assertEqual(announce(), "meow")


class ProtocolRegistrationTestCase(unittest.TestCase):
    """
    registering services under a protocol via provides=
    """

    def test_register_class_with_protocol_provides(self) -> None:
        """
        provides=Protocol is accepted when the class satisfies it.
        """
        registry = Registry()
        definition = registry.register(Dog, provides=Speaker)
        self.assertIs(definition.provides, Speaker)
        self.assertIsInstance(registry.get(Speaker), Dog)  # type: ignore[type-abstract]

    def test_register_class_not_satisfying_protocol(self) -> None:
        """
        provides=Protocol is rejected when the class does not satisfy it.
        """
        registry = Registry()
        with self.assertRaises(DefinitionError):
            registry.register(Anvil, provides=Speaker)

    def test_register_instance_with_protocol_provides(self) -> None:
        """
        register_instance verifies the instance against the protocol.
        """
        registry = Registry()
        dog = Dog()
        registry.register_instance(dog, provides=Speaker)
        self.assertIs(registry.get(Speaker), dog)  # type: ignore[type-abstract]

    def test_register_instance_not_satisfying_protocol(self) -> None:
        """
        register_instance rejects instances that miss protocol members.
        """
        registry = Registry()
        with self.assertRaises(DefinitionError):
            registry.register_instance(Anvil(), provides=Speaker)


class ProtocolLimitsTestCase(unittest.TestCase):
    """
    the two protocol shapes that cannot be matched structurally
    """

    def test_non_runtime_checkable_lookup_raises(self) -> None:
        """
        Looking up a protocol without @runtime_checkable is a clear error.
        """
        registry = Registry()
        registry.register(Dog)
        with self.assertRaises(ServiceError) as caught:
            registry.get(Quiet)  # type: ignore[type-abstract]
        self.assertIn("runtime_checkable", str(caught.exception))

    def test_non_runtime_checkable_provides_raises(self) -> None:
        """
        provides= rejects protocols without @runtime_checkable, both flavors.
        """
        registry = Registry()
        with self.assertRaises(DefinitionError):
            registry.register(Dog, provides=Quiet)
        with self.assertRaises(DefinitionError):
            registry.register_instance(Dog(), provides=Quiet)

    def test_data_member_protocol_lookup_raises(self) -> None:
        """
        Structurally matching a data-member protocol is a clear error.
        """
        registry = Registry()
        registry.register(Config)
        with self.assertRaises(ServiceError) as caught:
            registry.get(HasDsn)  # type: ignore[type-abstract]
        self.assertIn("structurally", str(caught.exception))

    def test_data_member_protocol_exact_registration(self) -> None:
        """
        An exact provides=Protocol registration still resolves by identity.
        """
        registry = Registry()
        config = Config()
        # isinstance() does verify data members, so registration checks work
        registry.register_instance(config, provides=HasDsn)
        self.assertIs(registry.get(HasDsn), config)  # type: ignore[type-abstract]
        with self.assertRaises(DefinitionError):
            registry.register_instance(Anvil(), provides=HasDsn, name="broken")


if __name__ == "__main__":
    unittest.main()
