"""update_owner_server.bat refuses to restart the server unless the venv can
import its dependencies. That list has to be this app's requirements, no more
and no fewer: up to v1.9.0 it named jose and passlib - Boord's sign-in
libraries, which this app has never installed - so every update on the farm
stopped there with a healthy server, and the owner was told "no internet".

Copied from Boord Labour's tests/test_updater.py, which caught the same
mistake there. The one difference is that a requirement's install name is not
always its import name, so the few that differ are mapped below.
"""
import importlib
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# pip name -> import name, for the requirements where the two differ.
IMPORT_NAME = {"python-multipart": "multipart"}


def _required_imports() -> set:
    with open(os.path.join(ROOT, "backend", "requirements.txt"), encoding="utf-8") as fh:
        names = {re.split(r"[\[=<>~ ]", line.strip())[0].lower()
                 for line in fh if line.strip() and not line.lstrip().startswith("#")}
    return {IMPORT_NAME.get(n, n) for n in names}


def _checked_imports() -> set:
    with open(os.path.join(ROOT, "update_owner_server.bat"), encoding="utf-8") as fh:
        checked = re.search(r'-c "import ([a-z_, ]+)"', fh.read()).group(1)
    return {m.strip() for m in checked.split(",")}


def test_updater_import_check_matches_requirements():
    assert _checked_imports() == _required_imports()


def test_updater_import_check_names_real_modules():
    # The installed venv is the one the tests run in, so every name the
    # updater asks for must actually import here - a typo, or an import name
    # that is not the pip name, would otherwise only show up on the farm.
    for name in sorted(_checked_imports()):
        importlib.import_module(name)
