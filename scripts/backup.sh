#!/usr/bin/env bash
set -euo pipefail
umask 077

if (( EUID != 0 )); then
  echo "Запустите скрипт от root: нужен доступ к Docker и rclone.conf" >&2
  exit 1
fi

cd /root/apps/time-agent

rclone_config=/root/.config/rclone/rclone.conf
if [[ ! -r "$rclone_config" ]]; then
  echo "Не найден доступный rclone.conf: $rclone_config" >&2
  exit 1
fi

# Не допускаем одновременного ручного и автоматического запуска.
exec 9>/run/lock/time-agent-backup.lock
if ! flock -n 9; then
  echo "Другой бэкап уже выполняется" >&2
  exit 1
fi

workdir=$(mktemp -d /root/time-agent-backup.XXXXXXXX)
trap 'rm -rf -- "$workdir"' EXIT

filename="agent-$(date -u +%Y%m%dT%H%M%SZ)-$(cat /proc/sys/kernel/random/uuid).sqlite3"

# Создаём согласованный снимок работающей SQLite-базы.
# Python запускается вместо main.py: второй экземпляр бота не стартует.
docker compose run --rm --no-deps --user 0:0 \
  --entrypoint python \
  -v "$workdir:/backup" \
  -T app - "$filename" <<'PY'
import os
import sqlite3
import sys
from pathlib import Path

source = Path("/app/data/agent.sqlite3")
target = Path("/backup") / sys.argv[1]

if not source.is_file():
    raise SystemExit("Рабочая SQLite-база не найдена")
if target.exists():
    raise SystemExit("Файл снимка уже существует")

with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
    with sqlite3.connect(target) as dst:
        src.backup(dst)

with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as db:
    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    tables = {
        row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

if integrity != "ok" or not {"entries", "action_log"} <= tables:
    raise SystemExit(
        f"Снимок не прошёл проверку: integrity={integrity}, tables={tables}"
    )

os.chmod(target, 0o600)
print("Локальный снимок SQLite проверен")
PY

# crypt шифрует содержимое и имя файла до отправки в Google Drive.
rclone --config "$rclone_config" \
  copyto "$workdir/$filename" "gdrive_crypt:$filename"

# Проверяем, что облачную копию можно скачать, расшифровать и что
# её байты совпадают с уже проверенным снимком SQLite.
rclone --config "$rclone_config" \
  copyto "gdrive_crypt:$filename" "$workdir/returned.sqlite3"
cmp "$workdir/$filename" "$workdir/returned.sqlite3"

echo "Бэкап успешно создан и проверен: $filename"
