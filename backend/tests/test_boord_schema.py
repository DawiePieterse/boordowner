"""The Owner app's picture of Boord's schema, checked against Boord itself.

The rest of the suite builds its fake boord.db from models_boord.py, which
means the mirror is only ever validated against itself - it cannot notice
that the real Boord has moved. That is exactly how this app came to be
pinned to v2.14's `systemsetting.farm_name` while Boord shipped v3.1 with
`packhouse_name`, and how a green suite sat next to a service that refused
to boot on the farm.

So this module reads ../Boord/backend/models.py - the authoritative
definitions - and checks every column main._BOORD_SCHEMA_CHECK claims to
read is actually there. It skips when that checkout isn't beside this repo,
so a lone clone still passes, and it fails the moment somebody with both
repos open pulls a Boord release that renames something.

Parsed with `ast` rather than imported: Boord's models.py pulls in its own
dependencies and would register a second, conflicting set of tables on the
shared SQLModel.metadata this app's models already own.
"""
import ast
import os

import pytest

import config
import main

BOORD_MODELS = os.path.join(config.REPO_ROOT, "..", "Boord", "backend", "models.py")

# SQLModel classes whose table name is the lower-cased class name.
_CLASS_FOR_TABLE = {
    "block": "Block",
    "worker": "Worker",
    "supplier": "Supplier",
    "systemsetting": "SystemSetting",
    "lot": "Lot",
    "harvestrecord": "HarvestRecord",
}


def _boord_columns() -> dict:
    """{table name: set of column names} from Boord's own models.py."""
    with open(BOORD_MODELS) as f:
        tree = ast.parse(f.read())
    by_class = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        fields = {stmt.target.id for stmt in node.body
                  if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)}
        by_class[node.name] = fields
    return {table: by_class.get(cls, set()) for table, cls in _CLASS_FOR_TABLE.items()}


needs_boord = pytest.mark.skipif(
    not os.path.exists(BOORD_MODELS),
    reason=f"no Boord checkout at {BOORD_MODELS} - nothing to check the mirror against")


@needs_boord
def test_schema_check_names_only_columns_boord_actually_has():
    """Every column main._BOORD_SCHEMA_CHECK selects exists in Boord today.

    A failure here means Boord has renamed or dropped something this app
    reads. Update models_boord.py and _BOORD_SCHEMA_CHECK together, then move
    the verified-release range in README.md and main.py's comment.
    """
    boord = _boord_columns()
    missing = {}
    for table, columns in main._BOORD_SCHEMA_CHECK.items():
        wanted = {c.strip() for c in columns.split(",")}
        gone = wanted - boord[table]
        if gone:
            missing[table] = sorted(gone)
    assert not missing, (
        f"Boord no longer has these columns this app reads: {missing}. "
        f"See {BOORD_MODELS}.")


@needs_boord
def test_mirror_and_schema_check_agree():
    """models_boord.py and _BOORD_SCHEMA_CHECK are two lists of the same
    thing, kept by hand. They drift apart quietly, and then the boot check
    stops covering a column the app really does read."""
    import models_boord

    for table, cls_name in _CLASS_FOR_TABLE.items():
        mirrored = set(getattr(models_boord, cls_name).model_fields)
        checked = {c.strip() for c in main._BOORD_SCHEMA_CHECK[table].split(",")}
        assert mirrored == checked, (
            f"`{table}`: models_boord.{cls_name} has {sorted(mirrored)} but "
            f"main._BOORD_SCHEMA_CHECK asserts {sorted(checked)}")
