"""Prepare local collector settings and an importable Happ routing profile.

No network requests, installers, tunnel changes or credentials. Existing files
are preserved; a different configuration needs a new --config/--state-dir.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path

from preflight import ROOT, DEFAULT_CONFIG, check_xray, run_checks


class SetupError(ValueError):
    """Only fixed, safe instructions; never contains user JSON or credentials."""


def state_directory():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "T&L/collect-avito"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "tl-avito"


def happ_profile():
    # Omitted fields use Happ defaults. This is routing, not a server subscription.
    # https://www.happ.su/main/ru/dev-docs/routing (checked 2026-10-27).
    return {"Name": "T&L Avito direct", "GlobalProxy": "true",
            "RouteOrder": "direct-block-proxy",
            "DirectSites": ["domain:avito.ru", "domain:avito.st"]}


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def prepare(mode, config_path=DEFAULT_CONFIG, state_dir=None, xray_path=None,
            browse_skill=None):
    config_path = Path(config_path).expanduser().resolve()
    settings = {"tunnel_mode": mode, "xray_config_path": None}
    files = {}
    if browse_skill:
        browse_skill = Path(browse_skill).expanduser().resolve()
        if not browse_skill.is_file():
            raise SetupError("Укажите существующий SKILL.md gstack /browse через --browse-skill.")
        settings["browse_skill_path"] = str(browse_skill)
    if mode == "happ":
        directory = Path(state_dir or state_directory()).expanduser().resolve()
        if directory.is_relative_to(ROOT.resolve()):
            raise SetupError("Файлы Happ храните вне checkout проекта; выберите --state-dir вне него.")
        profile = happ_profile()
        profile_path = directory / "avito-routing.json"
        settings["happ_routing_path"] = str(profile_path)
        link = "happ://routing/add/" + base64.b64encode(
            json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode()
        ).decode()
        files[profile_path] = json_text(profile)
        files[directory / "avito-routing-link.txt"] = link + "\n"
    elif mode == "xray":
        if not xray_path:
            raise SetupError("Для xray нужен --xray-config: полный локальный JSON вне проекта.")
        private = Path(xray_path).expanduser().resolve()
        if private.is_relative_to(ROOT):
            raise SetupError("Полную Xray-конфигурацию храните вне проекта.")
        errors = check_xray(json.loads(private.read_text(encoding="utf-8")))
        if errors:
            raise SetupError("Правила Xray требуют исправления; см. VPN_HAPP.md. Исходник не изменён.")
        settings["xray_config_path"] = str(private)
    elif mode != "disabled":
        raise SetupError("Выберите disabled, happ или xray.")

    # Read all existing destinations before creating anything. Never reset a
    # teammate's settings on a repeated first-run invocation.
    if config_path.exists():
        existing = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict) or any(existing.get(k) != v for k, v in settings.items()):
            raise SetupError("Локальные настройки уже отличаются. Они сохранены; используйте другой --config.")
        settings = existing
    else:
        files[config_path] = json_text(settings)
    for path, content in files.items():
        if path.exists() and path.read_text(encoding="utf-8") != content:
            raise SetupError("Файл назначения уже изменён; выберите другой --state-dir/--config. Ничего не перезаписано.")
    created = []
    for path, content in files.items():
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("x", encoding="utf-8") as stream:
            if os.name != "nt":
                os.chmod(path, 0o600)
            stream.write(content)
        created.append(str(path))
    return {"status": "SETTINGS_PREPARED", "created": created,
            "config_path": str(config_path), "tunnel_mode": mode,
            "happ_import_required": mode == "happ", "live_route_verified": False,
            "next": ("Импортируйте avito-routing-link.txt в Happ и выберите профиль у своей подписки. "
                     "Остальной трафик этого нового профиля по умолчанию идёт в proxy; пользовательские "
                     "правила прежнего профиля не перенесены. Затем preflight.py --probe-aside --strict-routing "
                     "с тем же --config; успешность файла не доказывает импорт или действующий маршрут."
                     if mode == "happ" else "Запустите preflight.py --probe-aside; затем первую обычную загрузку сайта.")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["disabled", "happ", "xray"])
    parser.add_argument("--config", type=Path,
                        default=Path(os.environ.get("TL_AVITO_CONFIG", str(DEFAULT_CONFIG))))
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--xray-config", type=Path)
    parser.add_argument("--browse-skill", type=Path)
    args = parser.parse_args()
    try:
        result = (prepare(args.mode, args.config, args.state_dir, args.xray_config, args.browse_skill)
                  if args.mode else run_checks(args.config))
    except SetupError as error:
        print(json_text({"status": "NEEDS_SETUP", "next": str(error)}), end="")
        return 2
    except (OSError, ValueError, TypeError):
        # Do not print exception payloads: a malformed supplied JSON can contain secrets.
        print(json_text({"status": "NEEDS_SETUP", "next":
              "Проверьте режим, доступность путей и существующие файлы. Изменённые настройки не перезаписываются; "
              "используйте новый --config/--state-dir. Для Xray нужен валидный полный JSON вне проекта."}), end="")
        return 2
    print(json_text(result), end="")
    return 2 if result["status"] == "NEEDS_SETUP" else 0


if __name__ == "__main__":
    raise SystemExit(main())
