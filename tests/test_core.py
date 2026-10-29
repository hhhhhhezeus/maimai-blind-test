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
            self.assertEqual(result['feedback'][selected], ['反馈'])
            self.assertTrue(all('identity' in item for item in store.view(reviewer)['items']))

if __name__ == '__main__':
    unittest.main()
