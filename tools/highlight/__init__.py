"""Syntax highlighting foundations for the RXF languages.

- ``meaning.json``: the one meaning table (leaves, operators, enums, references).
- ``meaning.py``: loads the table.
- ``grammar_gen.py``: the build step that derives the TextMate grammars of
  ``editors/rxf-highlight`` from the table.
- ``resolve.py``: the reusable resolver of the references of a ``.rxfd`` delta
  document (pure Python, no UI).

``tests/test_highlight.py`` pins every transcribed part of the table back to its
source of truth and keeps the generated grammars up to date.

The submodules are not imported here on purpose: ``python3 -m
tools.highlight.grammar_gen`` runs the build step without importing it twice.
"""
