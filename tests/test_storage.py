import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.storage import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'agent.sqlite3'
        with sqlite3.connect(self.db) as conn:
            conn.execute('CREATE TABLE entries(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,kind TEXT NOT NULL,text TEXT NOT NULL,created_at TEXT NOT NULL)')
            conn.execute('CREATE TABLE action_log(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,action TEXT NOT NULL,details TEXT NOT NULL,created_at TEXT NOT NULL)')
            conn.execute("INSERT INTO entries(user_id,kind,text,created_at) VALUES (2,'wish','old','2025-01-01T00:00:00+00:00')")
        self.s = Store(self.db, 1)
        self.s.init()
        self.s.init()

    def test_migration_preserves_entries_and_restricts_non_owner(self):
        self.assertEqual(self.s.touch(2, 'User', '')['status'], 'pending')
        with self.assertRaises(PermissionError):
            self.s.entries(2)
        with self.assertRaises(PermissionError):
            self.s.add(2, 'task', 'x')
        self.assertEqual(self.s.entries(1, target=2, mode='all')[0]['text'], 'old')
        self.assertEqual(self.s.entries(1, target=2, mode='all')[0]['created_at'], '2025-01-01T00:00:00+00:00')

    def test_grant_revoke_regrant_and_continue(self):
        with self.assertRaises(PermissionError):
            self.s.grant(2, 2)
        self.s.grant(1, 2)
        with self.assertRaises(PermissionError):
            self.s.add(2, 'task', 'new')
        self.assertEqual(len(self.s.entries(2, mode='all')), 1)
        self.s.choose(2)
        self.assertEqual(self.s.entries(2)[0]['text'], 'old')
        self.s.add(2, 'task', 'new')
        self.s.revoke(1, 2, 'requested removal')
        self.assertEqual(self.s.users(1, 'revoked')[0]['reason'], 'requested removal')
        with self.assertRaises(PermissionError):
            self.s.entries(2, mode='all')
        self.s.grant(1, 2)
        self.assertEqual(self.s.touch(2, 'User', '')['needs_choice'], 1)
        self.assertEqual(len(self.s.entries(2, mode='all')), 2)
        self.s.choose(2)
        self.assertEqual(len(self.s.entries(2)), 2)
        self.assertEqual([e['action'] for e in self.s.events(1, 2)], ['grant', 'revoke', 'grant'])

    def test_newstart_archives_without_deleting_and_is_isolated(self):
        self.s.grant(1, 2)
        self.s.choose(2, fresh=True)
        self.assertEqual(self.s.entries(2), [])
        self.assertEqual(self.s.entries(2, mode='archive')[0]['text'], 'old')
        self.assertEqual(next(u for u in self.s.users(1, 'active') if u['user_id'] == 2)['archive_count'], 1)
        self.s.touch(3, 'Other', '')
        self.s.grant(1, 3)
        with self.assertRaises(PermissionError):
            self.s.entries(3, target=2, mode='all')
        with self.assertRaises(PermissionError):
            self.s.users(3, 'active')
        with self.assertRaises(ValueError):
            self.s.revoke(1, 1, 'no')

    def test_pending_revoked_and_empty_admin_actions(self):
        self.s.touch(3, 'Other', '')
        self.assertEqual({u['user_id'] for u in self.s.users(1, 'pending')}, {2, 3})
        self.assertFalse(self.s.grant(1, 1))
        with self.assertRaises(ValueError):
            self.s.grant(1, 999)
        with self.assertRaises(ValueError):
            self.s.revoke(1, 3, '')
        self.s.revoke(1, 3, 'policy')
        self.assertEqual(self.s.touch(3, 'Other', '')['status'], 'revoked')


if __name__ == '__main__':
    unittest.main()
