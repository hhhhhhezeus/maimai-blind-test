import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.request import urlopen
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
                await plugin.private('qq', 'user', 'hello')
                self.assertEqual([c['capability'] for c in calls], ['chat.open_session', 'send.text'])
                self.assertEqual(calls[-1]['args']['stream_id'], 'private-stream')
            finally:
                await plugin.on_unload()
            self.assertIsNone(plugin.server)
