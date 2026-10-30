"""Small independent review page. Access tokens are sent in headers, never URLs to the server."""
from __future__ import annotations

import base64
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from .core import BlindError

PAGE = Path(__file__).with_name('index.html').read_bytes()
MAX_BODY = 22 * 1024 * 1024


def run(store, host, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Do not write bearer tokens, submissions or media into request logs.
            logging.getLogger('blind_test').debug('web request: %s', self.command)

        def send_bytes(self, status, body, content_type='application/json; charset=utf-8'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' blob:; media-src 'self' blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, value, status=200):
            self.send_bytes(status, json.dumps(value, ensure_ascii=False).encode())

        def token(self):
            value = self.headers.get('Authorization', '')
            if not value.startswith('Bearer ') or len(value) > 150:
                raise BlindError('请使用麦麦私聊发送的个人链接。')
            return value[7:]

        def do_GET(self):
            try:
                path = urlsplit(self.path).path
                if path == '/':
                    return self.send_bytes(200, PAGE, 'text/html; charset=utf-8')
                token = self.token()
                if path == '/api/view':
                    return self.send_json(store.view(token))
                if path.startswith('/api/media/'):
                    mime, data = store.media(token, path.rsplit('/', 1)[-1])
                    return self.send_bytes(200, data, mime)
                if path == '/api/results':
                    a = store.auth(token)
                    return self.send_json(store.results(a['round_id']))
                self.send_json({'error': '页面不存在。'}, 404)
            except BlindError as e:
                self.send_json({'error': str(e)}, 400)
            except Exception:
                logging.getLogger('blind_test').exception('web GET failed')
                self.send_json({'error': '服务暂时无法处理请求。'}, 500)

        def do_POST(self):
            try:
                token = self.token()
                size = int(self.headers.get('Content-Length', '0'))
                if size < 1 or size > MAX_BODY:
                    raise BlindError('请求过大或为空。')
                store.auth(token)
                obj = json.loads(self.rfile.read(size))
                if not isinstance(obj, dict):
                    raise BlindError('请求内容必须是 JSON 对象。')
                for field in ('identity', 'kind', 'text', 'mime', 'data', 'item_id'):
                    if field in obj and not isinstance(obj[field], str):
                        raise BlindError('请求字段类型无效。')
                for field in ('choices', 'comments'):
                    if field in obj and not isinstance(obj[field], dict):
                        raise BlindError('评测内容必须是 JSON 对象。')
                path = urlsplit(self.path).path
                if path == '/api/add':
                    data = base64.b64decode(obj.get('data', ''), validate=True)
                    store.add(token, obj.get('identity', ''), obj.get('kind', ''), obj.get('text', ''), obj.get('mime', ''), data)
                elif path == '/api/edit':
                    store.edit(token, obj.get('item_id', ''), obj.get('identity', ''), obj.get('text'))
                elif path == '/api/remove':
                    store.remove(token, obj.get('item_id', ''))
                elif path == '/api/start':
                    store.start(token)
                elif path == '/api/vote':
                    store.vote(token, obj.get('choices', {}), obj.get('comments', {}))
                elif path == '/api/reveal':
                    store.reveal(token)
                else:
                    return self.send_json({'error': '页面不存在。'}, 404)
                self.send_json({'ok': True})
            except (BlindError, ValueError, TypeError, json.JSONDecodeError) as e:
                self.send_json({'error': str(e) or '请求无效。'}, 400)
            except Exception:
                logging.getLogger('blind_test').exception('web POST failed')
                self.send_json({'error': '服务暂时无法处理请求。'}, 500)

    server = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, name='blind-review-web', daemon=True)
    thread.start()
    return server
