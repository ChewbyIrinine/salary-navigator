---
name: collect-avito
description: Set up and run the T&L Avito collector in Claude Code, including local Happ routing, bounded samples, reviewed CSV and the team publication workflow.
---

# Claude Code: сбор вакансий T&L

Это точка обнаружения навыка, не отдельная реализация парсера.
Найдите корень текущего checkout по `Scraper/extract.js` и прочитайте:

1. [CLAUDE.md](../../../CLAUDE.md) и [AGENTS.md](../../../AGENTS.md).
2. [Общий SKILL.md](../../../Scraper/skills/collect-avito/SKILL.md).
3. [HOSTS.md](../../../Scraper/skills/collect-avito/references/HOSTS.md), раздел Claude Code.

Используйте проектных агентов `avito-capture` (`model: haiku`) и
`avito-review` (`model: sonnet`) из `.claude/agents/`. Не используйте Codex
model ID и инструмент `collaboration.spawn_agent` в Claude Code.
Ведущий запускает оба этапа последовательно и ждёт результат каждого;
агенты этапов повторно себя не делегируют. Имена моделей в runner — запись
фактического исполнения, не команда переключения модели.

Для первого запуска следуйте общему `references/FIRST_RUN.md`. Скрипты,
экстрактор и данные остаются в `Scraper/` и `Data/` этого checkout. Скачайте
весь репозиторий: копирование одной `.claude/skills/collect-avito/` недостаточно.
Обычные параметры, фильтры, остановки и публикация определяются общим навыком.
