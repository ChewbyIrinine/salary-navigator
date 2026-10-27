# T&L: Claude Code

Общие инструкции проекта находятся в [AGENTS.md](AGENTS.md): прочитайте их
перед работой. Для задач сбора используйте `/collect-avito`, точка входа —
[.claude/skills/collect-avito/SKILL.md](.claude/skills/collect-avito/SKILL.md).

Каноническая инструкция — [Scraper/skills/collect-avito/SKILL.md](Scraper/skills/collect-avito/SKILL.md).
Claude-маршрут и карта файлов — [HOSTS.md](Scraper/skills/collect-avito/references/HOSTS.md).
В Claude Code браузерный пакет исполняет `avito-capture` (Haiku), проверку и
исключения — `avito-review` (Sonnet). Определения лежат в `.claude/agents/`.
Упоминания Luna/Terra относятся к Codex; не пытайтесь передавать их имена
в Claude `model`. Все команды runner выполняются из корня подготовленного
checkout, а не из каталога установленного навыка.

Веб-доступ только через gstack `/browse` и Aside. Не используйте Claude in
Chrome. Публикация, лимит ошибок и сохранность данных одинаковы для обоих
исполнителей. При новом сборе сначала обновите весь checkout по общему навыку;
при resume сохраните исходные хеши. Этот файл не даёт дополнительных разрешений.
