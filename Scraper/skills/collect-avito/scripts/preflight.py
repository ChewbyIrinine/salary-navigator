"""Read-only local checks for the agent's Aside workflow; never visits Avito."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONFIG = ROOT / "Scraper/collect-avito.local.json"


def check_xray(config):
    """Conservative static check: explicit domain rules, order and direct outbound.

    This cannot verify the configuration loaded by the running tunnel, DNS/IP
    matching or all Xray routing semantics. Ambiguous earlier rules need review.
    """
    if not isinstance(config, dict):
        return ["Корень Xray-конфигурации должен быть объектом."]
    routing = config.get("routing", {})
    rules = routing.get("rules", []) if isinstance(routing, dict) else None
    outbounds = config.get("outbounds", [])
    if (not isinstance(rules, list) or not all(isinstance(r, dict) for r in rules)
            or not isinstance(outbounds, list)
            or not all(isinstance(o, dict) for o in outbounds)):
        return ["Неизвестная структура routing.rules или outbounds."]
    tags = [o.get("tag") for o in outbounds]
    if not all(isinstance(tag, str) and tag for tag in tags) or len(tags) != len(set(tags)):
        return ["У выходов должны быть непустые уникальные теги; проверьте outbounds."]
    direct = {o["tag"] for o in outbounds if o.get("protocol") == "freedom"}
    errors = []
    for domain in ("avito.ru", "avito.st"):
        found = False
        for index, rule in enumerate(rules):
            network = rule.get("network", "tcp,udp")
            if not isinstance(network, str) or not set(network.split(",")) <= {"tcp", "udp"}:
                errors.append(f"{domain}: проверьте network в правиле {index + 1}.")
                break
            if network == "udp":
                continue  # A UDP-only block does not shadow a TCP direct rule.
            domains = rule.get("domain", [])
            if not isinstance(domains, list) or not all(isinstance(d, str) for d in domains):
                errors.append(f"{domain}: проверьте domain в правиле {index + 1}.")
                break
            explicit = f"domain:{domain}" in domains
            # Additional conditions may narrow an otherwise matching domain rule.
            conditions = set(rule) - {"type", "domain", "outboundTag", "ruleTag", "network"}
            if explicit and not conditions and rule.get("type") == "field":
                if rule.get("outboundTag") in direct and not rule.get("balancerTag"):
                    found = True
                else:
                    errors.append(f"{domain}: первое явное правило не ведёт в freedom/direct.")
                break
            # Only clearly unrelated full/domain rules can be skipped safely.
            unrelated = bool(domains) and all(
                (d.startswith("full:") and not (d[5:] == domain or d[5:].endswith("." + domain)))
                or (d.startswith("domain:") and not (
                    domain == d[7:] or domain.endswith("." + d[7:])
                    or d[7:].endswith("." + domain)))
                for d in domains
            )
            if not unrelated:
                errors.append(f"{domain}: правило {index + 1} перед direct требует проверки порядка/условий.")
                break
        else:
            errors.append(f"{domain}: нет явного доменного правила direct с покрытием поддоменов.")
        if not found and not any(e.startswith(domain + ":") for e in errors):
            errors.append(f"{domain}: прямой маршрут не подтверждён конфигурацией.")
    return errors


def find_browse_skill(root=ROOT):
    codex = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    candidates = [root / ".agents/skills/gstack/browse/SKILL.md",
                  codex / "skills/gstack/browse/SKILL.md",
                  codex / "skills/gstack-browse/SKILL.md"]
    if os.environ.get("GSTACK_ROOT"):
        candidates.insert(0, Path(os.environ["GSTACK_ROOT"]) / "browse/SKILL.md")
    return next((p for p in candidates if p.is_file()), None)


def aside_ready(executable):
    try:
        result = subprocess.run(
            [executable, "repl", 'console.log("TNL_ASIDE_READY")'],
            capture_output=True, text=True, timeout=20,
        )
        # Do not print CLI diagnostics: they may contain private session paths.
        return (result.returncode == 0
                and "TNL_ASIDE_READY" in result.stdout.splitlines()
                and "[error" not in (result.stdout + result.stderr).lower())
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_checks(config_path=DEFAULT_CONFIG, probe_aside=False, strict_routing=False):
    checks = []

    def add(name, ok, detail, fix="", *, blocking=True):
        checks.append({"check": name, "ok": ok, "detail": detail, "blocking": blocking,
                       **({"next_step": fix} if not ok else {})})

    add("python", sys.version_info >= (3, 10), "Нужен Python 3.10+.",
        "Установите Python 3.10+ и повторите команду этим интерпретатором.")
    required = ["Scraper/extract.js", "Scraper/skills/collect-avito/SKILL.md",
                "Scraper/ASIDE_WORKFLOW.md", "Scraper/templates/manifest.template.json"]
    add("project", all((ROOT / p).is_file() for p in required),
        "Проверка наличия файлов навыка, экстрактора и паспорта.",
        "Обновите checkout проекта; не копируйте один SKILL.md отдельно от файлов.")
    skill = find_browse_skill()
    add("gstack_browse", skill is not None, "Проверено наличие инструкции gstack /browse.",
        "Установите gstack /browse для своего агента; затем повторите проверку.")
    aside = shutil.which("aside")
    fallback = Path.home() / ".local/bin/aside"
    if not aside and fallback.is_file() and os.access(fallback, os.X_OK):
        aside = str(fallback)
    add("aside_cli", bool(aside), "Проверено наличие Aside CLI.",
        "Установите Aside (aside.com, macOS 15+) и настройте CLI по инструкции приложения.")
    if probe_aside and aside:
        add("aside_session", aside_ready(aside), "Проверка REPL без открытия вкладок и сайта.",
            "Откройте Aside и проверьте aside --help; затем повторите проверку. Подробности CLI остаются локально.")

    settings = None
    try:
        settings = json.loads(Path(config_path).read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            raise ValueError
    except (OSError, ValueError):
        add("local_config", False, "Локальные настройки отсутствуют или не читаются как JSON-объект.",
            "Для статического аудита укажите локальный конфиг; для обычного сбора используйте готовый Aside и первую загрузку сайта.",
            blocking=strict_routing)
    if settings is not None:
        mode = settings.get("tunnel_mode")
        add("tunnel_mode", mode in ("disabled", "xray"),
            "Режим туннеля должен быть указан явно: disabled или xray.",
            "Укажите disabled, если работаете без туннеля, или xray и путь к актуальному конфигу Happ/Xray.",
            blocking=strict_routing)
        if mode == "disabled":
            add("tunnel_config", True, "В локальных настройках заявлена работа без туннеля; состояние ОС и маршрут не проверены.")
        elif mode == "xray":
            path = settings.get("xray_config_path")
            try:
                if not isinstance(path, str) or not path.strip():
                    raise ValueError
                private_path = Path(path).expanduser()
                if not private_path.is_absolute() or private_path.resolve().is_relative_to(ROOT):
                    raise ValueError
                config = json.loads(private_path.read_text(encoding="utf-8"))
                errors = check_xray(config)
                add("tunnel_config", not errors,
                    "Проверка явных TCP-правил avito.ru/avito.st и freedom-outbound в файле; работающий туннель не проверяется.",
                    " ".join(errors) + " См. Scraper/VPN_HAPP.md; проверьте актуальный профиль и повторите команду.",
                    blocking=strict_routing)
            except (OSError, ValueError, TypeError):
                add("tunnel_config", False, "Не удалось прочитать локальную Xray-конфигурацию.",
                    "Укажите абсолютный xray_config_path к актуальному JSON вне репозитория. Полный конфиг не публикуйте.",
                    blocking=strict_routing)
    blocked = any(not c["ok"] and c["blocking"] for c in checks)
    warnings = any(not c["ok"] and not c["blocking"] for c in checks)
    status = "NEEDS_SETUP" if blocked else "READY_WITH_WARNINGS" if warnings else "LOCAL_CHECKS_OK"
    return {"status": status, "checks": checks,
            "aside_session_checked": bool(probe_aside and aside),
            "can_collect": not blocked and bool(probe_aside and aside),
            "live_route_verified": False,
            "strict_routing": strict_routing,
            "next": "Перед сбором: --probe-aside, собственная сессия /browse, одна обычная загрузка сайта. Предупреждение о конфиге не означает отказ браузера. При блокировке сайта остановитесь; при resume сначала checkpoint. Маршрут остаётся неподтверждённым без отдельного измерения."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=Path(os.environ.get("TL_AVITO_CONFIG", str(DEFAULT_CONFIG))))
    parser.add_argument("--probe-aside", action="store_true",
                        help="Readiness-only REPL; no tabs, navigation or Avito requests")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict-routing", action="store_true",
                        help="Require a valid static routing config when auditing network setup")
    args = parser.parse_args()
    result = run_checks(args.config, args.probe_aside, args.strict_routing)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["status"])
        for check in result["checks"]:
            label = "OK" if check["ok"] else "НУЖНО ДЕЙСТВИЕ" if check["blocking"] else "ПРЕДУПРЕЖДЕНИЕ"
            print(f"{label} · {check['check']}: {check['detail']}")
            if "next_step" in check:
                print("  " + check["next_step"])
        print(result["next"])
    return 2 if result["status"] == "NEEDS_SETUP" else 0


if __name__ == "__main__":
    raise SystemExit(main())
