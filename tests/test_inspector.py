"""The inspector: read-only views of an image, and the app that serves them."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pymergetic.rxf.core.object import Image

DELTA = """<?xml version="1.0" encoding="UTF-8"?>
<delta xmlns:rxf="urn:pymergetic:rxf">
  <Type name="Point" form="Form.Struct" size="16" align="8">
    <Field name="x" value_type="uint64_t"/>
    <Field name="next" value_type="uint64_t" offset="8" flags="FieldFlags.Optional|Ref"/>
  </Type>
  <Type name="Role" form="Form.Struct" size="0" align="1"/>
  <Role name="peer"/>
  <Point name="a" x="1" next="b">
    <rxf:ref role="peer" index="2" kind="RefKind.Data" target="c"/>
  </Point>
  <Point name="b" x="2"/>
  <Point name="c" x="3"/>
  <rxf:entry path="a"/>
</delta>
"""


@pytest.fixture
def inspector():
    from pymergetic.rxf.server.inspector import Inspector

    image = Image.empty()
    image.apply(DELTA)
    return Inspector(image)


def test_inspector_navigates_the_tree(inspector):
    info = inspector.info()
    assert info["issue_count"] == 0 and info["entry"] == "a"
    names = [row["name"] for row in inspector.children("")["items"]]
    assert names == sorted(
        [
            *(
                "Type",
                "Field",
                "uint32_t",
                "uint64_t",
                "Point",
                "Role",
                "peer",
                "a",
                "b",
                "c",
            ),
            *("Enumerator", "NodeKind", "Form", "TypeFlags", "FieldFlags"),
            *("RefKind", "Binding", "OwnerKind", "Mobility"),
        ]
    )
    assert [row["name"] for row in inspector.children("Point")["items"]] == [
        "next",
        "x",
    ]
    assert inspector.children("", offset=3, limit=2)["items"][0]["name"] == names[3]
    assert len(inspector.children("", offset=3, limit=2)["items"]) == 2
    assert inspector.search("point") == [{"path": "Point", "type": "Type"}]
    with pytest.raises(KeyError):
        inspector.object("nothing")


def test_inspector_shows_an_object_and_who_uses_it(inspector):
    a = inspector.object("a")
    assert (
        a["type"] == "Point" and a["type_path"] == "Point" and a["parent"] == ""
    )  # the root
    assert {f["name"]: f["value"] for f in a["fields"]} == {"x": "1", "next": "b"}
    b = inspector.object("b")
    assert b["referrers"] == [{"path": "a", "how": "field"}]
    assert inspector.object("c")["referrers"] == [{"path": "a", "how": "ref"}]
    (ref,) = a["refs"]
    assert (ref["role"], ref["index"], ref["target"]) == ("peer", 2, "c")
    assert {r["how"] for r in inspector.object("peer")["referrers"]} == {"role"}
    # A Type is used by its Fields and by the objects of that type.
    users = {r["path"] for r in inspector.object("uint64_t")["referrers"]}
    assert {"Point.x", "Point.next"} <= users


def test_the_inspector_shows_where_an_alias_is_used():
    from pymergetic.rxf.core import tree
    from pymergetic.rxf.server.inspector import Inspector

    starter = Inspector(tree.load(Path(__file__).parents[1] / "starter"))
    alias = starter.object("pymergetic.rxf.numeric.numeric")
    users = [r for r in alias["referrers"] if r["how"] == "alias"]
    assert users and alias["referrer_count"] >= len(users)
    # and the other way round: an object says which aliases it is written with
    user = starter.object(users[0]["path"])
    assert "pymergetic.rxf.numeric.numeric" in user["aliases"]
    assert starter.object("pymergetic.rxf")["aliases"] == []


def test_the_inspector_app_serves_the_routes(inspector):
    from fastapi.routing import APIRoute

    from pymergetic.rxf.server.app import PAGE, create_app

    app = create_app(inspector.image)
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    paths = {route.path for route in routes}
    assert {
        "/",
        "/model",
        "/api/info",
        "/api/children",
        "/api/object",
        "/api/search",
        "/api/delta",
    } <= paths
    handlers = {route.path: route.endpoint for route in routes}
    assert handlers["/api/object"]("a")["path"] == "a"
    assert handlers["/api/delta"]().body.decode().startswith("<?xml")
    with pytest.raises(Exception, match="no object"):
        handlers["/api/object"]("zzz")
    assert "<title>RXF inspector</title>" in handlers["/"]()
    assert '<a class="button" href="/model"' in handlers["/"]()
    assert PAGE.is_file()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_inspector_script_parses(tmp_path):
    from pymergetic.rxf.server.app import PAGE

    found = re.search(r"<script>(.*)</script>", PAGE.read_text(), re.DOTALL)
    assert found is not None
    script = found.group(1)
    path = tmp_path / "page.js"
    path.write_text(script)
    result = subprocess.run(
        ["node", "--check", str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
