"""Local, single-owner social scheduler. Python 3.11+; no dependencies."""
import base64
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from integration import Publisher, IntegrationRequired, PublishRejected

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('DATA_DIR', ROOT / 'data')).resolve()
DB = DATA / 'scheduler.sqlite3'
SESSIONS = {}
MAX_BODY = 36 * 1024 * 1024

def now():
    return datetime.now(timezone.utc).isoformat()

def connect():
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c

def init():
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / 'media').mkdir(exist_ok=True)
    with connect() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, name TEXT NOT NULL,
          platform TEXT NOT NULL CHECK(platform IN ('facebook','instagram')), external_id TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS media(id TEXT PRIMARY KEY, name TEXT, mime TEXT, size INTEGER);
        CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY, caption TEXT NOT NULL, media_id TEXT REFERENCES media(id),
          scheduled_at TEXT, timezone TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY, post_id TEXT REFERENCES posts(id) ON DELETE CASCADE,
          account_id TEXT REFERENCES accounts(id), status TEXT NOT NULL, attempts INTEGER DEFAULT 0,
          external_post_id TEXT, error TEXT, updated_at TEXT, UNIQUE(post_id,account_id));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, delivery_id TEXT, message TEXT, created_at TEXT);
        ''')
        # An interrupted API request has an unknown outcome. Never automatically send it again.
        c.execute("UPDATE deliveries SET status='unknown', error='Server stopped during publishing. Verify remotely before retrying.', updated_at=? WHERE status='publishing'", (now(),))

def validate_post(c, body):
    caption = str(body.get('caption', '')).strip()
    ids = body.get('account_ids', [])
    if not isinstance(ids, list) or not 1 <= len(ids) <= 3 or len(set(ids)) != len(ids):
        raise ValueError('Choose between one and three different accounts.')
    rows = c.execute('SELECT * FROM accounts WHERE id IN (%s)' % ','.join('?'*len(ids)), ids).fetchall()
    if len(rows) != len(ids):
        raise ValueError('One of the selected accounts no longer exists.')
    if not caption or len(caption) > 2200:
        raise ValueError('Caption must contain 1–2,200 characters.')
    media_id = body.get('media_id') or None
    if media_id and not c.execute('SELECT id FROM media WHERE id=?', (media_id,)).fetchone():
        raise ValueError('Media does not exist.')
    if any(a['platform'] == 'instagram' for a in rows) and not media_id:
        raise ValueError('Instagram posts require an image or video.')
    zone = str(body.get('timezone', 'UTC'))
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo(zone)
    except Exception:
        raise ValueError('Choose a valid IANA timezone.')
    draft = body.get('draft') is True
    at = body.get('scheduled_at')
    if not draft:
        try:
            dt = datetime.fromisoformat(at.replace('Z', '+00:00'))
            if dt.tzinfo is None or dt <= datetime.now(timezone.utc):
                raise ValueError()
            at = dt.astimezone(timezone.utc).isoformat()
        except Exception:
            raise ValueError('Choose a future publishing time with a timezone.')
    else:
        at = None
    return caption, ids, media_id, at, zone, 'draft' if draft else 'scheduled'

def save_post(body, post_id=None):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        values = validate_post(c, body)
        caption, ids, media_id, at, zone, state = values
        if post_id:
            post = c.execute('SELECT * FROM posts WHERE id=?', (post_id,)).fetchone()
            if not post:
                raise ValueError('Post not found.')
            if post['state'] not in ('draft','scheduled') or c.execute("SELECT 1 FROM deliveries WHERE post_id=? AND status NOT IN ('pending','cancelled')", (post_id,)).fetchone():
                raise ValueError('This post has started processing and cannot be edited.')
            c.execute('UPDATE posts SET caption=?,media_id=?,scheduled_at=?,timezone=?,state=? WHERE id=?', (caption, media_id, at, zone, state, post_id))
            c.execute('DELETE FROM deliveries WHERE post_id=?', (post_id,))
        else:
            post_id = secrets.token_hex(12)
            c.execute('INSERT INTO posts VALUES(?,?,?,?,?,?,?)', (post_id, caption, media_id, at, zone, state, now()))
        for aid in ids:
            c.execute('INSERT INTO deliveries(id,post_id,account_id,status,updated_at) VALUES(?,?,?,?,?)', (secrets.token_hex(12),post_id,aid,'pending',now()))
    return post_id

def tick(publisher=None):
    publisher = publisher or Publisher()
    # Claim exactly one delivery in a transaction. Separate process workers can safely claim different deliveries.
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        d = c.execute("""SELECT d.*,p.caption,p.media_id,p.timezone,a.name,a.platform,a.external_id
          FROM deliveries d JOIN posts p ON d.post_id=p.id JOIN accounts a ON d.account_id=a.id
          WHERE p.state='scheduled' AND p.scheduled_at<=? AND d.status='pending'
          ORDER BY p.scheduled_at,d.id LIMIT 1""", (now(),)).fetchone()
        if not d:
            return False
        d = dict(d)
        c.execute("UPDATE deliveries SET status='publishing',attempts=attempts+1,updated_at=? WHERE id=?", (now(), d['id']))
    try:
        media = None
        if d['media_id']:
            with connect() as c:
                media = dict(c.execute('SELECT * FROM media WHERE id=?', (d['media_id'],)).fetchone())
            media['path'] = str(DATA / 'media' / media['id'])
        result = publisher.publish(account={'id':d['account_id'],'name':d['name'],'platform':d['platform'],'external_id':d['external_id']},
            caption=d['caption'], media=media, idempotency_key=d['id'])
        if not isinstance(result, str) or not result:
            raise RuntimeError('Adapter did not return a confirmed remote post ID.')
        status, error, remote = 'published', None, result
    except IntegrationRequired as e:
        status, error, remote = 'blocked', str(e), None
    except PublishRejected as e:
        status, error, remote = 'failed', str(e), None
    except Exception:
        status, error, remote = 'unknown', 'Publishing outcome uncertain. Verify the account before retrying; see server logs.', None
        import traceback
        traceback.print_exc()
    with connect() as c:
        c.execute('UPDATE deliveries SET status=?,error=?,external_post_id=?,updated_at=? WHERE id=?', (status,error,remote,now(),d['id']))
        c.execute('INSERT INTO events(delivery_id,message,created_at) VALUES(?,?,?)', (d['id'], error or ('Published: '+remote), now()))
    return True

def worker():
    while True:
        try:
            if not tick():
                time.sleep(2)
        except Exception:
            import traceback
            traceback.print_exc()
            time.sleep(5)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Do not log query strings or request bodies.
        print(self.command, urlparse(self.path).path, args[1] if len(args)>1 else '')

    def send(self, status, value, mime='application/json'):
        data = json.dumps(value).encode() if mime=='application/json' else value
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)

    def session(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie',''))
            sid = cookie['scheduler_session'].value
            item = SESSIONS.get(sid)
            if item and item['expires'] > time.time():
                return item
            SESSIONS.pop(sid, None)
        except Exception:
            pass
        return None

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ('/','/app.js','/style.css'):
            file = ROOT / 'static' / ('index.html' if path=='/' else path[1:])
            return self.send(200,file.read_bytes(),mimetypes.guess_type(file.name)[0] or 'text/plain')
        session = self.session()
        if not session:
            return self.send(401,{'error':'Please sign in.'})
        if path == '/api/state':
            with connect() as c:
                accounts = [dict(r) for r in c.execute('SELECT * FROM accounts ORDER BY name')]
                posts = [dict(r) for r in c.execute('SELECT * FROM posts ORDER BY created_at DESC')]
                for p in posts:
                    p['deliveries'] = [dict(r) for r in c.execute('SELECT d.*,a.name,a.platform FROM deliveries d JOIN accounts a ON d.account_id=a.id WHERE post_id=?', (p['id'],))]
                    p['media'] = dict(c.execute('SELECT * FROM media WHERE id=?',(p['media_id'],)).fetchone()) if p['media_id'] else None
                events = [dict(r) for r in c.execute('SELECT * FROM events ORDER BY id DESC LIMIT 50')]
            return self.send(200,dict(accounts=accounts,posts=posts,events=events,csrf=session['csrf'],integration='not configured; implement integration.py'))
        if path.startswith('/media/'):
            mid = path[7:]
            with connect() as c:
                media = c.execute('SELECT * FROM media WHERE id=?',(mid,)).fetchone()
            if media:
                return self.send(200,(DATA/'media'/mid).read_bytes(),media['mime'])
        return self.send(404,{'error':'Not found.'})

    def do_POST(self):
        path = urlparse(self.path).path
        # Fetch Metadata + CSRF protect cookie-authenticated writes. No CORS allowed.
        if self.headers.get('Sec-Fetch-Site') not in (None,'same-origin','none'):
            return self.send(403, {'error':'Cross-site requests are not allowed.'})
        origin = self.headers.get('Origin')
        if origin and urlparse(origin).netloc != self.headers.get('Host'):
            return self.send(403, {'error':'Origin mismatch.'})
        try:
            size = int(self.headers.get('Content-Length','0'))
            if size <= 0 or size > MAX_BODY:
                return self.send(413, {'error':'Request is too large (25 MB media maximum).'})
            body = json.loads(self.rfile.read(size))
            if not isinstance(body,dict):
                raise ValueError('Expected a JSON object.')
            if path == '/api/login':
                # Fixed response delay reduces rapid local password guessing.
                time.sleep(0.25)
                if not hmac.compare_digest(str(body.get('password','')),PASSWORD):
                    return self.send(401,{'error':'Incorrect password.'})
                for key in list(SESSIONS):
                    if SESSIONS[key]['expires'] < time.time():
                        SESSIONS.pop(key,None)
                sid = secrets.token_urlsafe(32)
                csrf = secrets.token_urlsafe(32)
                SESSIONS[sid] = dict(expires=time.time()+43200,csrf=csrf)
                data = json.dumps({'ok':True}).encode()
                self.send_response(200)
                secure = '; Secure' if os.environ.get('COOKIE_SECURE')=='1' else ''
                self.send_header('Set-Cookie',f'scheduler_session={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200{secure}')
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(data)))
                self.send_header('Cache-Control','no-store')
                self.end_headers()
                self.wfile.write(data)
                return
            session = self.session()
            if not session:
                return self.send(401,{'error':'Please sign in.'})
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''), session['csrf']):
                return self.send(403,{'error':'Invalid security token. Refresh and retry.'})
            if path == '/api/logout':
                session['expires']=0
            elif path == '/api/accounts':
                name = str(body.get('name','')).strip()
                platform = body.get('platform')
                if not name or len(name)>80 or platform not in ('facebook','instagram'):
                    raise ValueError('Enter an account name and a valid platform.')
                with connect() as c:
                    if body.get('id'):
                        if not c.execute('SELECT 1 FROM accounts WHERE id=?',(body['id'],)).fetchone():
                            raise ValueError('Account not found.')
                        if c.execute("SELECT 1 FROM deliveries WHERE account_id=? AND status='publishing'",(body['id'],)).fetchone():
                            raise ValueError('Wait for publishing to finish before editing this account.')
                        old=c.execute('SELECT platform FROM accounts WHERE id=?',(body['id'],)).fetchone()
                        if old['platform'] != platform:
                            raise ValueError('Account platform cannot change. Create a different destination instead.')
                        c.execute('UPDATE accounts SET name=?,external_id=? WHERE id=?',(name,str(body.get('external_id','')).strip()[:150],body['id']))
                        return self.send(200,{'ok':True})
                    if c.execute('SELECT count(*) FROM accounts').fetchone()[0]>=3:
                        raise ValueError('This workspace supports three accounts. Remove an unused account first.')
                    c.execute('INSERT INTO accounts VALUES(?,?,?,?)',(secrets.token_hex(12),name,platform,str(body.get('external_id','')).strip()[:150]))
            elif path == '/api/accounts/delete':
                with connect() as c:
                    if c.execute('SELECT 1 FROM deliveries WHERE account_id=?',(body.get('id'),)).fetchone():
                        raise ValueError('This account has post history and cannot be removed.')
                    c.execute('DELETE FROM accounts WHERE id=?',(body.get('id'),))
            elif path == '/api/media':
                try:
                    raw = base64.b64decode(body.get('data',''),validate=True)
                except Exception:
                    raise ValueError('Invalid upload.')
                if not raw or len(raw)>25*1024*1024:
                    raise ValueError('Upload must be between 1 byte and 25 MB.')
                if raw.startswith(b'\xff\xd8\xff'):
                    mime='image/jpeg'
                elif raw.startswith(b'\x89PNG\r\n\x1a\n'):
                    mime='image/png'
                elif len(raw)>12 and raw[4:8]==b'ftyp':
                    mime='video/mp4'
                else:
                    raise ValueError('Supported uploads: JPEG, PNG, and MP4. File signature must match.')
                mid = secrets.token_hex(16)
                (DATA/'media'/mid).write_bytes(raw)
                with connect() as c:
                    c.execute('INSERT INTO media VALUES(?,?,?,?)',(mid,str(body.get('name','upload'))[:200],mime,len(raw)))
                return self.send(200,dict(id=mid,mime=mime,name=str(body.get('name','upload'))[:200]))
            elif path == '/api/posts':
                return self.send(200, {'id':save_post(body,body.get('id'))})
            elif path == '/api/posts/cancel':
                with connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    post = c.execute('SELECT state FROM posts WHERE id=?',(body.get('id'),)).fetchone()
                    if not post:
                        raise ValueError('Post not found.')
                    if c.execute("SELECT 1 FROM deliveries WHERE post_id=? AND status='publishing'",(body.get('id'),)).fetchone():
                        raise ValueError('A delivery is publishing. Wait for it to finish before cancelling.')
                    c.execute("UPDATE posts SET state='cancelled' WHERE id=?",(body.get('id'),))
                    c.execute("UPDATE deliveries SET status='cancelled', updated_at=? WHERE post_id=? AND status IN ('pending','blocked','failed','unknown')",(now(),body.get('id')))
            elif path == '/api/deliveries/retry':
                with connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    d=c.execute('SELECT d.*,p.state FROM deliveries d JOIN posts p ON d.post_id=p.id WHERE d.id=?',(body.get('id'),)).fetchone()
                    if not d or d['state']=='cancelled' or d['status'] not in ('failed','blocked','unknown'):
                        raise ValueError('This delivery cannot be retried.')
                    if d['status']=='unknown' and body.get('verified_not_published') is not True:
                        raise ValueError('Confirm that this post was not published remotely before retrying.')
                    c.execute("UPDATE deliveries SET status='pending',error=NULL,updated_at=? WHERE id=?",(now(),d['id']))
                    c.execute('INSERT INTO events(delivery_id,message,created_at) VALUES(?,?,?)',(d['id'],'Manual retry requested; uncertain outcomes require remote verification.',now()))
            else:
                return self.send(404,{'error':'Not found.'})
            return self.send(200,{'ok':True})
        except (ValueError,TypeError,sqlite3.IntegrityError) as e:
            return self.send(400,{'error':str(e)})
        except Exception:
            import traceback
            traceback.print_exc()
            return self.send(500,{'error':'Internal error. Check the server console.'})

if __name__=='__main__':
    PASSWORD = os.environ.get('APP_PASSWORD','')
    if len(PASSWORD)<12:
        raise SystemExit('Set APP_PASSWORD to a unique password of at least 12 characters before starting.')
    init()
    threading.Thread(target=worker,daemon=True).start()
    host = os.environ.get('HOST','127.0.0.1')
    port = int(os.environ.get('PORT','8080'))
    print(f'Meta Scheduler: http://{host}:{port} — keep this process running for scheduling.')
    ThreadingHTTPServer((host,port),Handler).serve_forever()
