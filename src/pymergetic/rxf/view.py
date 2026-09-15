"""Text view of semantic graph, ownership and global sections."""

from pymergetic.rxf.model.container import Container
from pymergetic.rxf.model.module import derived_fqns


def tree(container: Container) -> str:
    lines = []
    fqns = derived_fqns(container.nodes)
    for node in container.nodes:
        lines.append(
            f"[{node.id}] {node.kind.name} {fqns[node.id] or node.name} owner={node.owner_kind.name} state={node.state.to_dict()}"
        )
        for section in node.sections:
            lines.append(
                f"    sec {section.name} global_off={section.off} size={section.size}"
            )
        for reference in node.refs:
            lines.append(f"    -> {reference.kind.name} node:{reference.target}")
    return "\n".join(lines)


def view(container: Container) -> str:
    """Compatibility name for the semantic tree view."""
    return tree(container)
