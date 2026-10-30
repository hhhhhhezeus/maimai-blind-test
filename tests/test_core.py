import importlib.util
import pathlib
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('blind_core', pathlib.Path(__file__).resolve().parents[1] / 'core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

class BlindTestFlow(unittest.TestCase):
    def test_vote_reveal_and_access(self):
        with tempfile.TemporaryDirectory() as directory:
            store = core.Store(pathlib.Path(directory))
            code = store.create('主题', 'owner', 'group', 'qq', 'stream')
            owner = store.issue(code, 'owner', 'owner', 'group', 'qq')
            store.add(owner, '作者A', 'text', text='作品A')
            store.add(owner, '作者B', 'text', text='作品B')
            store.start(owner)
            reviewer = store.issue(code, 'reviewer', 'user', 'group', 'qq')
            items = store.view(reviewer)['items']
            self.assertTrue(all('identity' not in item for item in items))
            selected = items[0]['id']
            store.vote(reviewer, {'natural': selected, 'fit': 'equal'}, {selected: '反馈'})
            with self.assertRaises(core.BlindError):
                store.vote(reviewer, {'natural': selected, 'fit': 'equal'}, {})
            with self.assertRaises(core.BlindError):
                store.results(code)
            store.reveal(owner)
            result = store.results(code)
            self.assertEqual(result['count'], 1)
            with self.assertRaises(core.BlindError):
                store.create('跨平台', 'owner', 'group', 'other', 'stream', parent=code)
            followup = store.create('合法复测', 'owner', 'group', 'qq', 'stream', parent=code)
            self.assertTrue(followup)
            self.assertEqual(result['feedback'][selected], ['反馈'])
            self.assertTrue(all('identity' in item for item in store.view(reviewer)['items']))

    def test_draft_edit_remove_and_started_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            store = core.Store(pathlib.Path(directory))
            code = store.create('test', 'owner', 'g', 'qq', 's')
            owner = store.issue(code, 'owner', 'owner', 'g', 'qq')
            for n in range(3): store.add(owner, f'作者{n}', 'text', text=f'内容{n}')
            items = store.view(owner)['items']
            store.edit(owner, items[0]['id'], '新作者', '新内容')
            self.assertEqual(store.view(owner)['items'][0]['text_value'], '新内容')
            other = store.create('other', 'owner', 'g', 'qq', 's')
            other_owner = store.issue(other, 'owner', 'owner', 'g', 'qq')
            with self.assertRaises(core.BlindError): store.remove(other_owner, items[0]['id'])
            store.remove(owner, items[-1]['id'])
            self.assertEqual(len(store.view(owner)['items']), 2)
            store.start(owner)
            reviewer = store.issue(code, 'reviewer', 'user', 'g', 'qq')
            for token in (owner, reviewer):
                with self.assertRaises(core.BlindError): store.edit(token, items[0]['id'], 'bad', 'bad')
                with self.assertRaises(core.BlindError): store.remove(token, items[0]['id'])
            self.assertEqual(store.view(owner)['items'][0]['identity'], '新作者')

if __name__ == '__main__':
    unittest.main()
