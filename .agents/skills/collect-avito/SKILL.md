---
name: collect-avito
description: Set up and run the T&L Avito collector in Codex, including local Happ routing, bounded samples, reviewed CSV and the team publication workflow.
---

# Codex: сбор вакансий T&L

Это точка обнаружения навыка; общий код не копируется в личный каталог.
Найдите корень текущего checkout по `Scraper/extract.js` и прочитайте:

1. [AGENTS.md](../../../AGENTS.md).
2. [Общий SKILL.md](../../../Scraper/skills/collect-avito/SKILL.md).
3. [HOSTS.md](../../../Scraper/skills/collect-avito/references/HOSTS.md), раздел Codex.

Маршрут Codex: `gpt-6-luna`, low, для браузерного пакета;
`gpt-6.1-terra`, medium, для проверки/исключений и PR. Для первого запуска
следуйте общему `references/FIRST_RUN.md`. Весь код запускается из корня
подготовленного checkout, не из `.agents/skills/collect-avito/`.
Скачайте весь репозиторий; этот короткий файл сам по себе сборщик не содержит.
