"""ai-clipper: the data-root bootstrap must never redirect tests to the real project folders."""

import importlib
import os
from pathlib import Path

import aiclipper_env


def test_apply_is_noop_under_tests():
    before = dict(os.environ)
    aiclipper_env.apply()
    assert dict(os.environ) == before


def test_importing_cli_keeps_throwaway_workspace():
    importlib.import_module("cli")
    from sharkyclips import config

    real_projects = aiclipper_env.paths()["projects"].resolve()
    ws = config.workspace_dir().resolve()
    assert ws != real_projects and real_projects not in ws.parents
    assert Path(os.environ["SHARKYCLIPS_HOME"]).resolve() != aiclipper_env.paths()["home"].resolve()


def test_paths_are_relative_to_project_root():
    p = aiclipper_env.paths()
    assert p["root"] == Path(aiclipper_env.__file__).resolve().parents[2]
    assert p["models"] == p["root"] / "app" / "models"
