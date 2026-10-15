"""The live session behind `rxf serve` and `rxf live`: the world of a running image, registered
at its link so `rxf console` attaches to it (`session.py`; docs/19, "who runs when")."""

from .session import Session

__all__ = ["Session"]
