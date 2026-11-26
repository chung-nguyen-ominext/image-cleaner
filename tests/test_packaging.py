"""Static guards for the PyInstaller spec.

Both checks here cover bugs that only show up in packaged builds, where our
regular test-suite cannot see them.
"""

from __future__ import annotations

import ast
from pathlib import Path

SPEC = Path(__file__).resolve().parents[1] / "imagecleaner.spec"


def _spec_tree() -> ast.Module:
    return ast.parse(SPEC.read_text(encoding="utf-8"))


def _calls(tree: ast.Module, name: str) -> list[ast.Call]:
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    ]


def _keywords(call: ast.Call) -> dict[str, ast.expr]:
    return {kw.arg: kw.value for kw in call.keywords if kw.arg}


def test_onedir_exes_do_not_bundle_python_twice() -> None:
    """Without exclude_binaries the onedir bootloader looks for libpython in /tmp."""
    exes = _calls(_spec_tree(), "EXE")
    assert len(exes) == 2
    for exe in exes:
        flag = _keywords(exe).get("exclude_binaries")
        assert isinstance(flag, ast.Constant) and flag.value is True


def test_macos_app_is_not_marked_background_only() -> None:
    """A background-only .app cannot activate on macOS and gets no keystrokes."""
    bundles = _calls(_spec_tree(), "BUNDLE")
    assert len(bundles) == 1
    info_node = _keywords(bundles[0]).get("info_plist")
    assert isinstance(info_node, ast.Dict)
    values = {
        key.value: value for key, value in zip(info_node.keys, info_node.values)
        if isinstance(key, ast.Constant)
    }
    background_only = values["LSBackgroundOnly"]
    assert isinstance(background_only, ast.Constant) and background_only.value is False
    high_res = values["NSHighResolutionCapable"]
    assert isinstance(high_res, ast.Constant) and high_res.value is True
