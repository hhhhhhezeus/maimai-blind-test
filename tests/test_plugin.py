import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
import json
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from maibot_sdk.context import PluginContext, PluginPaths

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('blind_plugin', ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from blind_plugin.plugin import BlindTestPlugin
from blind_plugin.core import BlindError

class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifecycle_http_and_private_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            async def rpc(method, plugin_id, payload, **kwargs):
                calls.append(payload)
                if payload['capability'] == 'chat.open_session':
                    return {'success': True, 'stream_id': 'private-stream'}
                return {'success': True}
            plugin = BlindTestPlugin()
            plugin._set_context(PluginContext('test', rpc_call=rpc, paths=PluginPaths(data_dir=Path(directory), runtime_dir=Path(directory)/'runtime')))
            plugin.set_plugin_config({'plugin': {'config_version': '1.0.0', 'bind_host': '127.0.0.1', 'bind_port': 0}})
            await plugin.on_load()
            try:
                port = plugin.server.server_address[1]
                with urlopen(f'http://127.0.0.1:{port}/', timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn('text/html', response.headers['Content-Type'])
                code = plugin.store.create('test', 'owner', 'group', 'qq', 'stream')
                token = plugin.store.issue(code, 'owner', 'owner', 'group', 'qq')
                for payload in ([], None, {'choices': []}, {'identity': 123}):
                    request = Request(f'http://127.0.0.1:{port}/api/vote',
                        data=json.dumps(payload).encode(),
                        headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(request, timeout=3)
                    self.assertEqual(caught.exception.code, 400)
                    caught.exception.close()
                plugin.store.add(token, '原作者', 'text', text='原内容')
                item = plugin.store.view(token)['items'][0]['id']
                for route, payload in [('edit', {'item_id': item, 'identity': '新作者', 'text': '新内容'}), ('remove', {'item_id': item})]:
                    request = Request(f'http://127.0.0.1:{port}/api/{route}', data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
                    with urlopen(request, timeout=3) as response:
                        self.assertTrue(json.load(response)['ok'])
                self.assertEqual(plugin.store.view(token)['items'], [])
                await plugin.command(stream_id='group-stream', user_id='member', group_id='group', platform='qq', matched_groups={'args': '状态 '+code})
                self.assertIn('准备中', calls[-1]['args']['text'])
                self.assertIn('作品 0 件', calls[-1]['args']['text'])
                calls.clear()
                await plugin.private('qq', 'user', 'hello')
                self.assertEqual([c['capability'] for c in calls], ['chat.open_session', 'send.text'])
                self.assertEqual(calls[-1]['args']['stream_id'], 'private-stream')
            finally:
                await plugin.on_unload()
            self.assertIsNone(plugin.server)
