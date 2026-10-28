import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path, owner):
        self.path = Path(path)
        self.owner = owner

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            with db:
                yield db
        finally:
            db.close()

    def init(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL)')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(entries)')}
            if 'archived_at' not in columns:
                db.execute('ALTER TABLE entries ADD COLUMN archived_at TEXT')
            db.execute('CREATE TABLE IF NOT EXISTS action_log (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT \'\', username TEXT NOT NULL DEFAULT \'\', status TEXT NOT NULL, first_seen TEXT NOT NULL, updated_at TEXT NOT NULL, reason TEXT NOT NULL DEFAULT \'\', needs_choice INTEGER NOT NULL DEFAULT 0)')
            db.execute('CREATE TABLE IF NOT EXISTS access_events (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, actor_id INTEGER NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS entries_owner_archive ON entries(user_id, archived_at, id)')
            stamp = now()
            # Existing non-owner records remain stored but grant no automatic access.
            db.execute("INSERT OR IGNORE INTO users(user_id,status,first_seen,updated_at) SELECT DISTINCT user_id,'pending',created_at,? FROM entries", (stamp,))
            db.execute("INSERT OR IGNORE INTO users(user_id,status,first_seen,updated_at) VALUES (?,'active',?,?)", (self.owner, stamp, stamp))
            db.execute("UPDATE users SET status='active',needs_choice=0,reason='' WHERE user_id=?", (self.owner,))

    def touch(self, uid, name, username):
        with self.db() as db:
            stamp = now()
            db.execute("INSERT OR IGNORE INTO users(user_id,name,username,status,first_seen,updated_at) VALUES (?,?,?,'pending',?,?)", (uid, name, username or '', stamp, stamp))
            db.execute('UPDATE users SET name=?,username=? WHERE user_id=?', (name, username or '', uid))
            return dict(db.execute('SELECT * FROM users WHERE user_id=?', (uid,)).fetchone())

    def _access(self, db, uid, choice=False):
        row = db.execute('SELECT * FROM users WHERE user_id=?', (uid,)).fetchone()
        if not row or row['status'] != 'active':
            raise PermissionError('Доступ не разрешён.')
        if row['needs_choice'] and not choice:
            raise PermissionError('Сначала выберите /continue или /newstart CONFIRM. История: /history')

    def _admin(self, actor):
        if actor != self.owner:
            raise PermissionError('Команда доступна только администратору.')

    def _log(self, db, uid, action, details):
        db.execute('INSERT INTO action_log(user_id,action,details,created_at) VALUES (?,?,?,?)', (uid, action, details, now()))

    def grant(self, actor, uid):
        self._admin(actor)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM users WHERE user_id=?', (uid,)).fetchone()
            if not row:
                raise ValueError('Пользователь ещё не обращался к боту. Попросите его отправить /start.')
            if row['status'] == 'active':
                return False
            count = db.execute('SELECT COUNT(*) FROM entries WHERE user_id=?', (uid,)).fetchone()[0]
            stamp = now()
            db.execute("UPDATE users SET status='active',reason='',needs_choice=?,updated_at=? WHERE user_id=?", (int(count > 0), stamp, uid))
            db.execute('INSERT INTO access_events(user_id,actor_id,action,reason,created_at) VALUES (?,?,\'grant\',\'\',?)', (uid, actor, stamp))
            return True

    def revoke(self, actor, uid, reason):
        self._admin(actor)
        if uid == self.owner:
            raise ValueError('Нельзя отозвать доступ у администратора.')
        if not reason.strip() or len(reason) > 500:
            raise ValueError('Укажите причину от 1 до 500 символов.')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM users WHERE user_id=?', (uid,)).fetchone():
                raise ValueError('Пользователь не найден.')
            stamp = now()
            db.execute("UPDATE users SET status='revoked',reason=?,needs_choice=0,updated_at=? WHERE user_id=?", (reason, stamp, uid))
            db.execute('INSERT INTO access_events(user_id,actor_id,action,reason,created_at) VALUES (?,?,\'revoke\',?,?)', (uid, actor, reason, stamp))

    def choose(self, uid, fresh=False):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._access(db, uid, choice=True)
            row = db.execute('SELECT needs_choice FROM users WHERE user_id=?', (uid,)).fetchone()
            if not row[0]:
                raise ValueError('Выбор доступен только после повторного допуска с сохранённой историей.')
            if fresh:
                db.execute('UPDATE entries SET archived_at=? WHERE user_id=? AND archived_at IS NULL', (now(), uid))
            db.execute('UPDATE users SET needs_choice=0 WHERE user_id=?', (uid,))
            self._log(db, uid, 'newstart' if fresh else 'continue', 'history_preserved')

    def add(self, uid, kind, text):
        if kind not in ('wish', 'goal', 'task') or not text.strip() or len(text) > 2000:
            raise ValueError('Текст должен содержать от 1 до 2000 символов.')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._access(db, uid)
            cur = db.execute('INSERT INTO entries(user_id,kind,text,created_at) VALUES (?,?,?,?)', (uid, kind, text, now()))
            eid = cur.lastrowid
            self._log(db, uid, 'create_entry', f'{kind}:{eid}')
            return eid

    def entries(self, actor, target=None, mode='active', page=1):
        target = actor if target is None else target
        if target != actor:
            self._admin(actor)
        if mode not in ('active', 'archive', 'all') or page < 1:
            raise ValueError('Неверный режим или страница.')
        with self.db() as db:
            if target == actor:
                self._access(db, actor, choice=mode != 'active')
            suffix = {'active': ' AND archived_at IS NULL', 'archive': ' AND archived_at IS NOT NULL', 'all': ''}[mode]
            rows = db.execute('SELECT * FROM entries WHERE user_id=?' + suffix + ' ORDER BY id DESC LIMIT 10 OFFSET ?', (target, (page-1)*10)).fetchall()
            self._log(db, actor, 'view_entries', f'target={target};mode={mode};page={page}')
            return [dict(r) for r in rows]

    def users(self, actor, status, page=1):
        self._admin(actor)
        if status not in ('pending', 'active', 'revoked') or page < 1:
            raise ValueError('Статусы: pending, active, revoked. Страница: положительное число.')
        with self.db() as db:
            rows = db.execute('SELECT u.*, (SELECT COUNT(*) FROM entries e WHERE e.user_id=u.user_id AND e.archived_at IS NULL) AS active_count, (SELECT COUNT(*) FROM entries e WHERE e.user_id=u.user_id AND e.archived_at IS NOT NULL) AS archive_count FROM users u WHERE status=? ORDER BY updated_at DESC,user_id LIMIT 10 OFFSET ?', (status, (page-1)*10)).fetchall()
            return [dict(r) for r in rows]

    def events(self, actor, uid, page=1):
        self._admin(actor)
        if page < 1:
            raise ValueError('Страница: положительное число.')
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM access_events WHERE user_id=? ORDER BY id DESC LIMIT 10 OFFSET ?', (uid, (page-1)*10))]
