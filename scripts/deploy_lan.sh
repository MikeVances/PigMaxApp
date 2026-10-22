#!/usr/bin/env bash
# deploy_lan.sh — обновление PigMaxApp на внутреннем сервере (AGR Deploy, /srv/pigmax).
# Перенесён из MINMAXapp (scripts/deploy_lan.sh); отличие — порт берётся из unit-файла службы.
#
# Запуск на сервере:
#   sudo /srv/pigmax/scripts/deploy_lan.sh            # обновить до origin/main
#   sudo /srv/pigmax/scripts/deploy_lan.sh --dry-run  # только показать, что придёт
#   sudo /srv/pigmax/scripts/deploy_lan.sh --force    # перезапустить, даже если новых коммитов нет
#
# С рабочего компьютера одной командой:
#   ssh -t cdto@192.168.10.245 sudo /srv/pigmax/scripts/deploy_lan.sh
#
# Что делает:
#   1. git и pip — от имени ВЛАДЕЛЬЦА репозитория: иначе git отказывает
#      («dubious ownership»), а pip портит права в .venv.
#   2. Ручные правки на сервере не стираются — уходят в git stash с датой.
#   3. pull --ff-only: никаких merge-коммитов на сервере.
#   4. pip install -r requirements.txt — версии закреплены, лишнего не поставит.
#   5. restart службы, ожидание /healthz, сверка коммита в /version с только что
#      стянутым — так видно, что работает именно новый код, а не старый процесс.
#   6. Служба не поднялась → откат на прежнюю ревизию, pip, restart и журнал.
#
# Настройки можно переопределить переменными окружения (APP_DIR, SERVICE, PORT,
# BRANCH, REMOTE, OWNER, SYSTEMCTL). PORT по умолчанию — из «--port N» в ExecStart
# службы: на сервере он не записан в репозитории, а угадывать нельзя — по этому
# порту скрипт решает, поднялась ли новая версия, и при ошибке откатывает её.

set -euo pipefail

# Весь код — в функции main, вызов в последней строке. git pull перезаписывает
# этот файл, пока bash его выполняет, а bash читает скрипт по ходу исполнения:
# без функции он продолжил бы читать уже НОВЫЙ файл с середины. Функцию bash
# разбирает целиком до запуска.
main() {
    local APP_DIR="${APP_DIR:-/srv/pigmax}"
    local SERVICE="${SERVICE:-pigmax.service}"
    local PORT="${PORT:-}"
    local BRANCH="${BRANCH:-main}"
    local REMOTE="${REMOTE:-origin}"
    local HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-30}"
    local SYSTEMCTL="${SYSTEMCTL:-systemctl}"
    local DRY_RUN=0 FORCE=0
    # Помощники ниже (install_and_restart, check_running, as_owner) видят эти local
    # через динамическую область видимости bash — вызываются только из main

    for arg in "$@"; do
        case "$arg" in
            --dry-run) DRY_RUN=1 ;;
            --force)   FORCE=1 ;;
            -h|--help) sed -n '2,24p' "$0"; return 0 ;;
            *) echo "Неизвестный параметр: $arg (см. --help)" >&2; return 2 ;;
        esac
    done

    # ── Проверки до начала ─────────────────────────────────────────────────
    if [[ ! -d "$APP_DIR/.git" ]]; then
        echo "Нет git-репозитория в $APP_DIR" >&2
        return 1
    fi
    local OWNER="${OWNER:-$(stat -c '%U' "$APP_DIR")}"
    # root нужен для restart службы и sudo -u; сам владелец обходится без него
    if [[ $EUID -ne 0 && "$(id -un)" != "$OWNER" ]]; then
        echo "Нужны права root: sudo $0 $*" >&2
        return 1
    fi

    # git и pip — от имени владельца каталога (без sudo, если это он и запустил)
    as_owner() {
        if [[ "$(id -un)" == "$OWNER" ]]; then "$@"; else sudo -u "$OWNER" -- "$@"; fi
    }
    git_() { as_owner git -C "$APP_DIR" "$@"; }

    # Копия должна стоять на развёртываемой ветке: иначе merge влил бы main в чужую
    local CURRENT
    CURRENT="$(git_ rev-parse --abbrev-ref HEAD)"
    if [[ "$CURRENT" != "$BRANCH" ]]; then
        echo "На сервере выбрана ветка «${CURRENT}», а развёртывается «${BRANCH}» — остановлено." >&2
        return 1
    fi

    # venv проверяем ДО pull: иначе новый код лёг бы на диск, pip упал бы, а
    # служба осталась бы на старом процессе — расхождение, которое не видно снаружи
    if [[ ! -x "$APP_DIR/.venv/bin/pip" ]]; then
        echo "Нет $APP_DIR/.venv/bin/pip — скрипт ждёт venv в каталоге приложения." >&2
        return 1
    fi

    if [[ -z "$PORT" ]]; then
        PORT="$($SYSTEMCTL cat "$SERVICE" 2>/dev/null | sed -n 's/.*--port[ =]\([0-9][0-9]*\).*/\1/p' | head -1)"
        if [[ -z "$PORT" ]]; then
            echo "Не нашёл --port в ExecStart службы $SERVICE — задайте PORT=… явно." >&2
            return 1
        fi
    fi

    local OLD_REV
    OLD_REV="$(git_ rev-parse HEAD)"
    step "Сервер: $APP_DIR (владелец $OWNER), служба $SERVICE, порт $PORT, сейчас $(git_ log --oneline -1)"

    # ── Что придёт ─────────────────────────────────────────────────────────
    step "Получаю $REMOTE/$BRANCH"
    git_ fetch --quiet "$REMOTE" "$BRANCH"
    local INCOMING
    INCOMING="$(git_ log --oneline "HEAD..$REMOTE/$BRANCH")"
    if [[ -z "$INCOMING" ]]; then
        echo "Новых коммитов нет — уже $(git_ rev-parse --short HEAD)."
        if [[ $FORCE -eq 0 ]]; then
            check_running "$PORT" "$(git_ rev-parse --short=7 HEAD)" || true
            return 0
        fi
    else
        echo "Придут коммиты ($(echo "$INCOMING" | wc -l | tr -d ' ')):"
        while IFS= read -r line; do echo "    $line"; done <<< "$INCOMING"
    fi
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "--dry-run: ничего не меняю."
        return 0
    fi

    # ── Ручные правки на сервере — в stash, не в мусор ─────────────────────
    if [[ -n "$(git_ status --porcelain --untracked-files=no)" ]]; then
        local MSG
        MSG="deploy_lan.sh $(date '+%F %T'): правки на сервере до обновления"
        echo "На сервере есть изменённые файлы — откладываю в stash («${MSG}»):"
        git_ status --short --untracked-files=no | sed 's/^/    /'
        git_ stash push --quiet -m "$MSG"
        echo "Вернуть при необходимости: sudo -u $OWNER git -C $APP_DIR stash list / stash pop"
    fi

    # ── Обновление ─────────────────────────────────────────────────────────
    step "git pull --ff-only"
    git_ merge --ff-only --quiet "$REMOTE/$BRANCH"
    local NEW_SHORT
    NEW_SHORT="$(git_ rev-parse --short=7 HEAD)"

    install_and_restart

    if check_running "$PORT" "$NEW_SHORT"; then
        step "Готово: $(git_ log --oneline -1)"
        return 0
    fi

    # ── Откат ──────────────────────────────────────────────────────────────
    echo >&2
    echo "!!! Новая версия не поднялась — журнал службы:" >&2
    if command -v journalctl >/dev/null; then journalctl -u "$SERVICE" -n 30 --no-pager >&2 || true; fi
    step "Откат на ${OLD_REV:0:7}"
    git_ reset --quiet --hard "$OLD_REV"
    install_and_restart
    if check_running "$PORT" "${OLD_REV:0:7}"; then
        echo "Откат выполнен: работает прежняя версия ${OLD_REV:0:7}. Причина — в журнале выше." >&2
    else
        echo "!!! Откат тоже не поднялся — нужна ручная проверка: systemctl status $SERVICE" >&2
    fi
    return 1
}

# Зависимости и перезапуск — общая часть обновления и отката
install_and_restart() {
    step "pip install -r requirements.txt"
    as_owner "$APP_DIR/.venv/bin/pip" install --quiet --disable-pip-version-check -r "$APP_DIR/requirements.txt"
    step "systemctl restart $SERVICE"
    $SYSTEMCTL restart "$SERVICE"
}

# Ждёт /healthz и сверяет коммит в /version: ответ старого процесса не засчитывается
check_running() {
    local port="$1" expected="$2" deadline version
    deadline=$((SECONDS + HEALTH_TIMEOUT_S))
    step "Жду http://127.0.0.1:$port/healthz (до ${HEALTH_TIMEOUT_S} с)"
    until curl -fsS --max-time 2 "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; do
        if (( SECONDS >= deadline )); then
            echo "Служба не ответила за ${HEALTH_TIMEOUT_S} с." >&2
            return 1
        fi
        sleep 1
    done
    version="$(curl -fsS --max-time 5 "http://127.0.0.1:$port/version" || true)"
    echo "    /version: $version"
    # Коммит — разбором, а не сравнением строки: пробелы в JSON не должны решать исход
    local re='"commit"[[:space:]]*:[[:space:]]*"([^"]+)"' got=""
    [[ "$version" =~ $re ]] && got="${BASH_REMATCH[1]}"
    if [[ "$got" != "$expected"* ]]; then
        echo "Работает не тот коммит: ${got:-нет ответа}, ожидался $expected." >&2
        return 1
    fi
}

step() { printf '\n==> %s\n' "$*"; }

main "$@"; exit $?
