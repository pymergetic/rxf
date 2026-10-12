"""The words of the format: kinds, forms, flags."""

import enum


class NodeKind(enum.IntEnum):
    """What an object is (`rxf:kind`)."""

    Root = 0

    Group = 1

    Namespace = 2

    Card = 3

    Type = 4

    Field = 5

    Fn = 6

    Code = 7

    Data = 8

    Section = 10

    Limit = 11

    Import = 12

    Export = 13

    View = 14

    Tool = 15

    Blob = 16

    Channel = 17

    Transaction = 18

    Module = 19

    Enumerator = 20

    Alias = 21

class RefKind(enum.IntEnum):
    """What a reference is."""

    Call = 0

    Data = 1

    Entry = 2

    Type = 3

    Import = 4

    Implementation = 5

    Owner = 6

class Binding(enum.IntEnum):
    """Whether a reference must be set."""

    Mandatory = 0

    Optional = 1

class OwnerKind(enum.IntEnum):
    """Who owns an object."""

    System = 0

    User = 1

    Application = 2

    Build = 3

    Device = 4

    External = 5

    Shared = 6

class Mobility(enum.IntEnum):
    """Whether an object may move in memory."""

    Movable = 0

    Pinned = 1

    Relocatable = 2

class Form(enum.IntEnum):
    """The form of a Type."""

    Meta = 0

    Void = 1

    Bool = 2

    Uint = 3

    Sint = 4

    Float = 5

    Bytes = 6

    Struct = 7

    Union = 8

    Enum = 9

    Array = 10

    Ref = 11

    Function = 12

    Control = 13

    Expression = 14

    Execution = 15

    Reserved16 = 16

    Opaque = 17

class TypeFlags(enum.Flag):
    """The flags of a Type."""

    Variable = 1

    Signed = 2

    SelfDescribing = 4

    Bitset = 8

class FieldFlags(enum.Flag):
    """The flags of a Field."""

    Mutable = 1

    Optional = 2

    Repeated = 4

    Ref = 8

    Children = 16

    Inverse = 32
