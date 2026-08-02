"""显式配置解析与服务启动入口的单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

import server.config as config_mod
import server.launch as launch_mod
import server.main as main_mod
from server.config import Config, ConfigSource, load_config, resolve_config


def _write_config(path: Path, port: int) -> Path:
    path.write_text(f"[server]\nport = {port}\n", encoding="utf-8")
    return path


def test_load_config_strict_false_keeps_missing_path_defaults(tmp_path):
    cfg = load_config(tmp_path / "missing.toml", strict=False)
    assert cfg == Config()


def test_load_config_strict_rejects_missing_path(tmp_path):
    path = tmp_path / "missing.toml"
    with pytest.raises(FileNotFoundError, match="不存在"):
        load_config(path, strict=True)


def test_load_config_strict_rejects_unreadable_file(tmp_path, monkeypatch):
    path = _write_config(tmp_path / "config.toml", 9001)
    real_access = config_mod.os.access

    def fake_access(candidate, mode):
        if Path(candidate) == path:
            return False
        return real_access(candidate, mode)

    monkeypatch.setattr(config_mod.os, "access", fake_access)
    with pytest.raises(PermissionError, match="不可读"):
        load_config(path, strict=True)


def test_load_config_strict_rejects_invalid_toml(tmp_path):
    path = tmp_path / "invalid.toml"
    path.write_text("[server\nport = 9001\n", encoding="utf-8")
    with pytest.raises(ValueError, match="TOML 解析失败"):
        load_config(path, strict=True)


def test_resolve_config_cli_has_highest_priority_and_absolute_path(
    tmp_path, monkeypatch
):
    cli = _write_config(tmp_path / "cli.toml", 9001)
    env = _write_config(tmp_path / "env.toml", 9002)
    repo = _write_config(tmp_path / "repo.toml", 9003)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AI_USAGE_CONFIG", env.name)
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_PATH", repo)

    cfg, source = resolve_config(cli.name)

    assert cfg.server.port == 9001
    assert source == ConfigSource(path=cli.resolve(), origin="cli")
    assert source.path is not None and source.path.is_absolute()


def test_resolve_config_env_precedes_repo_and_absolute_path(tmp_path, monkeypatch):
    env = _write_config(tmp_path / "env.toml", 9002)
    repo = _write_config(tmp_path / "repo.toml", 9003)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AI_USAGE_CONFIG", env.name)
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_PATH", repo)

    cfg, source = resolve_config()

    assert cfg.server.port == 9002
    assert source == ConfigSource(path=env.resolve(), origin="env")
    assert source.path is not None and source.path.is_absolute()


def test_resolve_config_uses_repo_default(tmp_path, monkeypatch):
    repo = _write_config(tmp_path / "config.toml", 9003)
    monkeypatch.delenv("AI_USAGE_CONFIG", raising=False)
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_PATH", repo)

    cfg, source = resolve_config()

    assert cfg.server.port == 9003
    assert source == ConfigSource(path=repo.resolve(), origin="repo_default")


def test_resolve_config_uses_builtin_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_USAGE_CONFIG", raising=False)
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG_PATH", tmp_path / "missing.toml")

    cfg, source = resolve_config()

    assert cfg == Config()
    assert source == ConfigSource(path=None, origin="builtin")


def test_launch_strict_failure_exits_nonzero_with_reason(tmp_path, capsys):
    missing = tmp_path / "missing.toml"

    with pytest.raises(SystemExit) as exc_info:
        launch_mod.main(["--config", str(missing)])

    assert exc_info.value.code != 0
    assert "不存在" in capsys.readouterr().err


def test_launch_without_config_constructs_app(monkeypatch):
    cfg = Config()
    source = ConfigSource(path=None, origin="builtin")
    fake_app = object()
    calls: list[tuple] = []
    monkeypatch.setattr(
        launch_mod,
        "resolve_config",
        lambda path=None: calls.append(("resolve", path)) or (cfg, source),
    )
    monkeypatch.setattr(
        launch_mod,
        "create_app",
        lambda actual_cfg, actual_source: calls.append(
            ("create", actual_cfg, actual_source)
        )
        or fake_app,
    )
    monkeypatch.setattr(
        launch_mod.uvicorn,
        "run",
        lambda app, *, host, port: calls.append(("run", app, host, port)),
    )

    launch_mod.main([])

    assert calls == [
        ("resolve", None),
        ("create", cfg, source),
        ("run", fake_app, cfg.server.host, cfg.server.port),
    ]


def test_create_app_stores_optional_config_source():
    cfg = Config()
    source = ConfigSource(path=None, origin="builtin")

    app = main_mod.create_app(cfg, source)

    assert app.state.config_source is source


def test_main_resolves_once_before_creating_app(monkeypatch):
    cfg = Config()
    source = ConfigSource(path=None, origin="builtin")
    fake_app = object()
    calls: list[tuple] = []
    monkeypatch.setattr(
        main_mod,
        "resolve_config",
        lambda: calls.append(("resolve",)) or (cfg, source),
    )
    monkeypatch.setattr(
        main_mod,
        "create_app",
        lambda actual_cfg, actual_source: calls.append(
            ("create", actual_cfg, actual_source)
        )
        or fake_app,
    )
    monkeypatch.setattr(
        main_mod.uvicorn,
        "run",
        lambda app, *, host, port: calls.append(("run", app, host, port)),
    )

    main_mod.main()

    assert calls == [
        ("resolve",),
        ("create", cfg, source),
        ("run", fake_app, cfg.server.host, cfg.server.port),
    ]


def test_imported_main_module_has_no_module_level_app():
    assert not hasattr(main_mod, "app")
