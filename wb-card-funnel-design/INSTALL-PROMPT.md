# Команда для установки через Codex

Передайте человеку ссылку на репозиторий и этот текст:

> Установи скилл `wb-card-funnel-design` из `https://github.com/viteab-source/wb-card-funnel-design-skill`, путь в репозитории `wb-card-funnel-design`. После установки автоматически запусти его `scripts/bootstrap_runtime.py --json`, затем `scripts/run_tool.py doctor.py --json --strict-local`. Ничего не устанавливай системно и не проси пароли или токены. Покажи итог doctor и сообщи, доступен ли в текущей сессии `imagegen` и web/browser. После установки скилл будет доступен на следующем ходе.

Codex с системным `skill-installer` может установить каталог напрямую. Если такой возможности нет, репозиторий содержит корневой `install.py`, который копирует скилл в локальный Codex и сразу готовит runtime.
