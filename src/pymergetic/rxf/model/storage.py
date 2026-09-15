"""Global RXF heap geometry and image section models."""

from dataclasses import dataclass

from pymergetic.rxf.schema import SectionMap, SectionPerm


@dataclass
class HeapDef:
    """Geometry for the one RXF image and its committed address span."""

    image_size: int = 0
    committed_size: int = 0
    limit: int | None = None
    frontier: int = 0
    align: int = 8
    page_size: int = 4096

    def __post_init__(self) -> None:
        if self.image_size > self.committed_size:
            raise ValueError("image_size exceeds committed_size")
        if self.frontier > self.committed_size:
            raise ValueError("frontier exceeds committed_size")
        if self.limit is not None and self.committed_size > self.limit:
            raise ValueError("committed_size exceeds known heap limit")
        if self.align <= 0 or self.align & (self.align - 1):
            raise ValueError("heap alignment must be a positive power of two")
        if self.page_size <= 0 or self.page_size & (self.page_size - 1):
            raise ValueError("page_size must be a positive power of two")

    def to_dict(self) -> dict:
        result = {
            "image_size": self.image_size,
            "committed_size": self.committed_size,
            "frontier": self.frontier,
            "align": self.align,
            "page_size": self.page_size,
        }
        if self.limit is not None:
            result["limit"] = self.limit
        return result

    @classmethod
    def from_dict(cls, value: dict) -> "HeapDef":
        return cls(
            image_size=value.get("image_size", 0),
            committed_size=value.get("committed_size", 0),
            limit=value.get("limit"),
            frontier=value.get("frontier", 0),
            align=value.get("align", 8),
            page_size=value.get("page_size", 4096),
        )


@dataclass
class SectionDef:
    """A globally addressed image span owned by an ordinary node."""

    name: str
    off: int = 0
    size: int = 0
    map: SectionMap = SectionMap.DIRECT
    perm: SectionPerm = SectionPerm.R | SectionPerm.W

    def to_dict(self) -> dict:
        result = {"name": self.name, "off": self.off, "size": self.size}
        if self.map != SectionMap.DIRECT:
            result["map"] = self.map.value
        if self.perm != SectionPerm.R | SectionPerm.W:
            result["perm"] = self.perm.value
        return result

    @classmethod
    def from_dict(cls, value: dict) -> "SectionDef":
        if "domain" in value or "domain_node" in value:
            raise ValueError("section offsets are global; domain fields are obsolete")
        return cls(
            name=value["name"],
            off=value.get("off", 0),
            size=value.get("size", 0),
            map=SectionMap(value.get("map", SectionMap.DIRECT.value)),
            perm=SectionPerm(
                value.get("perm", SectionPerm.R.value | SectionPerm.W.value)
            ),
        )
