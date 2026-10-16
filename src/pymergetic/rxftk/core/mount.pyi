"""The object world as Python: classes, modules, instances."""



class Synthesizer:
    def __init__(self, image: object) -> None:
        """
        The mounted Python face of one image, synthesized from its self-description.
        """

    def names(self) -> list[str]:
        """Every module name of the image, in import spelling."""

    def module(self, name: str) -> object:
        """
        The module object of a module name (its exported names, lazily mounted).
        """

    def object(self, handle: int) -> object:
        """
        The mounted face of one object: a module for a Module cell, an instance otherwise.
        """

    def class_of(self, type: int) -> object:
        """The class synthesized for a Type object (one per Type, cached)."""

    def image(self) -> object:
        """The image every face reads."""
