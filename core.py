"""Persistence and state machine for private, independently ordered blind reviews."""
from __future__ import annotations

from contextlib import contextmanager

import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path


def digest(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


class BlindError(ValueError):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'blind.sqlite3'
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS rounds (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, owner TEXT NOT NULL,
              group_id TEXT NOT NULL, platform TEXT NOT NULL, stream_id TEXT NOT NULL,
              parent TEXT, state TEXT NOT NULL, created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS items (
              id TEXT PRIMARY KEY, round_id TEXT NOT NULL, identity TEXT NOT NULL,
              kind TEXT NOT NULL, text_value TEXT, mime TEXT, blob BLOB,
              created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS access (
              token_hash TEXT PRIMARY KEY, round_id TEXT NOT NULL, role TEXT NOT NULL,
              user_id TEXT NOT NULL, ordering TEXT, created INTEGER NOT NULL,
              UNIQUE(round_id, role, user_id));
            CREATE TABLE IF NOT EXISTS ballots (
              round_id TEXT NOT NULL, user_id TEXT NOT NULL, answers TEXT NOT NULL,
              submitted INTEGER NOT NULL, PRIMARY KEY(round_id, user_id));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, title, owner, group_id, platform, stream_id, parent=None):
        title = title.strip()
        if not title or len(title) > 100:
            raise BlindError('标题须为 1–100 字。')
        with self.connect() as db:
            if parent:
                previous = db.execute('SELECT * FROM rounds WHERE id=?', (parent,)).fetchone()
                if not previous or previous['owner'] != owner or previous['group_id'] != group_id or previous['state'] != 'revealed':
                    raise BlindError('只能关联本群由你主持且已经揭晓的活动。')
            code = secrets.token_hex(4).upper()
            db.execute('INSERT INTO rounds VALUES(?,?,?,?,?,?,?,?,?)',
                       (code, title, owner, group_id, platform, stream_id, parent, 'draft', int(time.time())))
        return code

    def issue(self, code, role, user, group, platform):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            r = db.execute('SELECT * FROM rounds WHERE id=?', (code,)).fetchone()
            if not r or r['group_id'] != group or r['platform'] != platform:
                raise BlindError('未找到本群活动。')
            if role == 'owner' and r['owner'] != user:
                raise BlindError('只有主持人可以管理。')
            if role == 'reviewer' and (r['state'] != 'open' or r['owner'] == user):
                raise BlindError('活动尚未开始、已结束，或主持人不能参与评测。')
            old = db.execute('SELECT ordering FROM access WHERE round_id=? AND role=? AND user_id=?', (code, role, user)).fetchone()
            ordering = old['ordering'] if old else None
            if role == 'reviewer' and not ordering:
                ids = [row['id'] for row in db.execute('SELECT id FROM items WHERE round_id=? ORDER BY created,id', (code,))]
                secrets.SystemRandom().shuffle(ids)
                ordering = json.dumps(ids)
            token = secrets.token_urlsafe(32)
            db.execute('DELETE FROM access WHERE round_id=? AND role=? AND user_id=?', (code, role, user))
            db.execute('INSERT INTO access VALUES(?,?,?,?,?,?)', (digest(token), code, role, user, ordering, int(time.time())))
            return token

    def auth(self, token):
        with self.connect() as db:
            result = db.execute('SELECT a.*, r.title, r.state, r.parent FROM access a JOIN rounds r ON r.id=a.round_id WHERE token_hash=?', (digest(token),)).fetchone()
            if not result:
                raise BlindError('链接无效或已被重新签发。')
            return dict(result)

    def add(self, token, identity, kind, text='', mime='', blob=b''):
        a = self.auth(token)
        identity = identity.strip()
        if a['role'] != 'owner' or a['state'] != 'draft':
            raise BlindError('只有主持人可在准备阶段投稿。')
        if not identity or len(identity) > 200:
            raise BlindError('揭晓信息须为 1–200 字。')
        if kind == 'text':
            if not text.strip() or len(text) > 6000:
                raise BlindError('文字作品须为 1–6000 字。')
            mime, blob = '', b''
        elif kind in ('image', 'audio'):
            allowed = {'image': {'image/jpeg', 'image/png', 'image/webp'}, 'audio': {'audio/mpeg', 'audio/wav', 'audio/ogg', 'audio/mp4', 'audio/webm'}}
            if mime not in allowed[kind] or not blob or len(blob) > 15 * 1024 * 1024:
                raise BlindError('文件类型不支持或超过 15 MB。')
            text = ''
        else:
            raise BlindError('作品类型不支持。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = db.execute('SELECT state FROM rounds WHERE id=?', (a['round_id'],)).fetchone()
            count = db.execute('SELECT COUNT(*) FROM items WHERE round_id=?', (a['round_id'],)).fetchone()[0]
            if not state or state['state'] != 'draft' or count >= 8:
                raise BlindError('投稿已关闭或达到 8 个作品。')
            db.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)', (secrets.token_hex(16), a['round_id'], identity, kind, text, mime, blob, time.time_ns()))

    def start(self, token):
        a = self.auth(token)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if a['role'] != 'owner' or db.execute('SELECT state FROM rounds WHERE id=?', (a['round_id'],)).fetchone()[0] != 'draft':
                raise BlindError('当前不能开始。')
            count = db.execute('SELECT COUNT(*) FROM items WHERE round_id=?', (a['round_id'],)).fetchone()[0]
            if count < 2:
                raise BlindError('至少需要两个作品。')
            db.execute("UPDATE rounds SET state='open' WHERE id=?", (a['round_id'],))

    def view(self, token):
        a = self.auth(token)
        with self.connect() as db:
            rows = [dict(x) for x in db.execute('SELECT id,kind,text_value,identity FROM items WHERE round_id=? ORDER BY created,id', (a['round_id'],))]
            if a['role'] == 'reviewer':
                if a['state'] not in ('open', 'revealed'):
                    raise BlindError('当前无法评测。')
                ordering = json.loads(a['ordering'])
                by_id = {x['id']: x for x in rows}
                rows = [by_id[x] for x in ordering if x in by_id]
                if a['state'] != 'revealed':
                    for item in rows:
                        item.pop('identity', None)
            voted = bool(db.execute('SELECT 1 FROM ballots WHERE round_id=? AND user_id=?', (a['round_id'], a['user_id'])).fetchone())
            return {'id': a['round_id'], 'title': a['title'], 'state': a['state'], 'role': a['role'], 'parent': a['parent'], 'items': rows, 'submitted': voted}

    def media(self, token, item_id):
        a = self.auth(token)
        with self.connect() as db:
            if a['role'] == 'reviewer' and item_id not in json.loads(a['ordering'] or '[]'):
                raise BlindError('无权访问作品。')
            row = db.execute('SELECT mime,blob FROM items WHERE id=? AND round_id=?', (item_id, a['round_id'])).fetchone()
            if not row or not row['blob']:
                raise BlindError('未找到素材。')
            return row['mime'], row['blob']

    def vote(self, token, choices, comments):
        a = self.auth(token)
        if a['role'] != 'reviewer':
            raise BlindError('主持人不能提交评测。')
        order = json.loads(a['ordering'] or '[]')
        for key in ('natural', 'fit'):
            if choices.get(key) not in order + ['equal', 'unknown']:
                raise BlindError('请完整选择两个问题。')
        if not isinstance(comments, dict) or any(k not in order or not isinstance(v, str) or len(v) > 500 for k, v in comments.items()):
            raise BlindError('评价内容无效或超过 500 字。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = db.execute('SELECT state FROM rounds WHERE id=?', (a['round_id'],)).fetchone()
            if not state or state[0] != 'open':
                raise BlindError('评测已经结束。')
            try:
                db.execute('INSERT INTO ballots VALUES(?,?,?,?)', (a['round_id'], a['user_id'], json.dumps({'choices': choices, 'comments': comments}, ensure_ascii=False), int(time.time())))
            except sqlite3.IntegrityError as e:
                raise BlindError('你已经提交过，不能重复提交。') from e

    def reveal(self, token):
        a = self.auth(token)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if a['role'] != 'owner':
                raise BlindError('只有主持人可以揭晓。')
            r = db.execute('SELECT state FROM rounds WHERE id=?', (a['round_id'],)).fetchone()
            if not r or r[0] != 'open':
                raise BlindError('只有进行中的活动可以揭晓。')
            db.execute("UPDATE rounds SET state='revealed' WHERE id=?", (a['round_id'],))

    def results(self, code):
        with self.connect() as db:
            r = db.execute('SELECT * FROM rounds WHERE id=?', (code,)).fetchone()
            if not r or r['state'] != 'revealed':
                raise BlindError('活动尚未揭晓。')
            items = [dict(x) for x in db.execute('SELECT id,identity,kind FROM items WHERE round_id=? ORDER BY created,id', (code,))]
            ballots = [json.loads(x[0]) for x in db.execute('SELECT answers FROM ballots WHERE round_id=?', (code,))]
            counts = {question: {x['id']: 0 for x in items} | {'equal': 0, 'unknown': 0} for question in ('natural', 'fit')}
            feedback = {x['id']: [] for x in items}
            for b in ballots:
                for question in counts:
                    value = b['choices'][question]
                    if value in counts[question]: counts[question][value] += 1
                for key, value in b['comments'].items():
                    if key in feedback and value.strip(): feedback[key].append(value.strip())
            result = {'title': r['title'], 'parent': r['parent'], 'items': items, 'count': len(ballots), 'counts': counts, 'feedback': feedback}
        if r['parent']:
            previous = self.results(r['parent'])
            result['parent_summary'] = {k: previous[k] for k in ('title', 'items', 'count', 'counts')}
        return result
