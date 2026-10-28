"""Prepare local collector settings and an importable Happ routing profile.

No network requests, installers, tunnel changes or credentials. Existing files
are preserved; a different configuration needs a new --config/--state-dir.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import os
from pathlib import Path

from preflight import ROOT, DEFAULT_CONFIG, check_xray, run_checks, default_config_path


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


def prepare_happ_tunnel(source_path, config_path=DEFAULT_CONFIG, state_dir=None,
                        browse_skill=None):
    """Preserve a supplied full Xray tunnel and derive a private Avito variant."""
    source = Path(source_path).expanduser().resolve()
    raw_directory = Path(state_dir or state_directory()).expanduser()
    raw_config = Path(config_path).expanduser()
    if raw_directory.is_symlink() or raw_config.is_symlink():
        raise SetupError("Каталог назначения и локальный конфиг не должны быть символическими ссылками.")
    directory = raw_directory.resolve()
    config_path = raw_config.resolve()
    if source.is_relative_to(ROOT.resolve()) or directory.is_relative_to(ROOT.resolve()):
        raise SetupError("Полный JSON туннеля и результат храните вне checkout проекта.")
    original = json.loads(source.read_text(encoding="utf-8"))
    if (not isinstance(original, dict) or not isinstance(original.get("outbounds"), list)
            or not original["outbounds"] or not all(isinstance(o, dict) for o in original["outbounds"])):
        raise SetupError("Нужен полный Xray JSON текущего туннеля Happ с outbounds; формат не распознан.")
    result = copy.deepcopy(original)
    outbounds = result["outbounds"]
    tags = [o.get("tag") for o in outbounds]
    if any(not isinstance(t, str) or not t for t in tags) or len(set(tags)) != len(tags):
        raise SetupError("У выходов нужны уникальные непустые теги; исходник сохранён.")
    routing = result.setdefault("routing", {})
    if not isinstance(routing, dict) or not isinstance(routing.get("rules", []), list):
        raise SetupError("Неизвестная структура routing.rules; исходник сохранён.")
    rules = routing.setdefault("rules", [])
    if not all(isinstance(rule, dict) for rule in rules):
        raise SetupError("Неизвестная структура routing.rules; исходник сохранён.")
    # Reuse only an unrestricted freedom outbound; custom direct settings stay intact.
    direct = next((o["tag"] for o in outbounds if o.get("protocol") == "freedom"
                   and set(o) <= {"tag", "protocol", "settings"}
                   and o.get("settings", {}) == {}), None)
    if direct is None:
        direct = "tl-avito-direct"
        while direct in tags:
            direct += "-1"
        # Append: the first/default outbound must retain its previous meaning.
        outbounds.append({"tag": direct, "protocol": "freedom", "settings": {}})
    rule = {"type": "field", "domain": ["domain:avito.ru", "domain:avito.st"],
            "outboundTag": direct}
    if not rules or rules[0] != rule:
        rules.insert(0, rule)
    if check_xray(result):
        raise SetupError("Не удалось подтвердить новые правила Авито; исходник сохранён.")
    output = directory / "avito-tunnel.json"
    settings = {"tunnel_mode": "xray", "xray_config_path": str(output),
                "tunnel_client": "happ"}
    if browse_skill:
        skill = Path(browse_skill).expanduser().resolve()
        if not skill.is_file():
            raise SetupError("Укажите существующий SKILL.md gstack /browse.")
        settings["browse_skill_path"] = str(skill)
    files = {directory / "current-tunnel.original.json": json_text(original),
             output: json_text(result)}
    if config_path.exists():
        existing = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict) or any(existing.get(k) != v for k, v in settings.items()):
            raise SetupError("Локальные настройки отличаются; выберите новый --config. Ничего не перезаписано.")
    else:
        files[config_path] = json_text(settings)
    # Check every destination before any write, including an input/output collision.
    for path, content in files.items():
        if path == source or (path.exists() and path.read_text(encoding="utf-8") != content):
            raise SetupError("Файл назначения занят или изменён; выберите новый --state-dir/--config.")
    if config_path.is_symlink():
        raise SetupError("Локальный конфиг не должен быть символической ссылкой.")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt" and directory.stat().st_mode & 0o077:
        raise SetupError("Выберите приватный --state-dir с правами 0700; существующий каталог не изменён.")
    if config_path.exists() and os.name != "nt":
        config_path.chmod(0o600)
    created = []
    for path, content in files.items():
        if path.is_symlink():
            raise SetupError("Файл назначения не должен быть символической ссылкой.")
        if path.exists():
            if os.name != "nt":
                path.chmod(0o600)
            continue
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Restrictive permissions apply at creation, even with umask 000.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        created.append(str(path))
    return {"status": "SETTINGS_PREPARED", "created": created,
            "config_path": str(config_path), "tunnel_mode": "xray", "tunnel_client": "happ",
            "import_file": str(output), "happ_import_required": True,
            "live_route_verified": False,
            "next": "Передайте avito-tunnel.json участнику для импорта полного JSON в Happ. "
                    "Импорт/активацию подтверждает участник; файл не доказывает действующий маршрут."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["disabled", "happ", "xray"])
    parser.add_argument("--config", type=Path,
                        default=default_config_path())
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--xray-config", type=Path)
    parser.add_argument("--happ-tunnel-json", type=Path,
                        help="Private full Xray JSON exported from Happ; derive an importable copy")
    parser.add_argument("--browse-skill", type=Path)
    args = parser.parse_args()
    try:
        if args.happ_tunnel_json:
            if args.mode not in (None, "xray") or args.xray_config:
                raise SetupError("Для --happ-tunnel-json используйте --mode xray без --xray-config.")
            result = prepare_happ_tunnel(args.happ_tunnel_json, args.config, args.state_dir, args.browse_skill)
        else:
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
