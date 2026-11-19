import unittest

from teclosync.retention import StateRotator


class MemoryStore:
    def __init__(self):
        self.pointers = {}
        self.releases = {}
        self.deleted_prefixes = []
        self.fail_current_once = False

    def read_current_pointer(self):
        return self.pointers.get('current/current.json')

    def read_previous_pointer(self):
        return self.pointers.get('previous/current.json')

    def copy_release(self, pointer, destination):
        run_id = pointer['run_id']
        copied = {
            **pointer,
            'manifest_key': f'{destination}/{run_id}/manifest.json',
            'state_key': f'{destination}/{run_id}/teclo-sync.db',
        }
        self.releases[(destination, run_id)] = copied
        return copied

    def write_pointer(self, name, payload):
        self.pointers[name] = payload
        if name == 'current/current.json' and self.fail_current_once:
            self.fail_current_once = False
            raise RuntimeError('ambiguous_current_write')

    def delete_pointer(self, name):
        self.pointers.pop(name, None)

    def delete_prefix(self, prefix):
        self.deleted_prefixes.append(prefix)
        return 1


class RetentionTests(unittest.TestCase):
    def test_first_publication_populates_current_only(self):
        store = MemoryStore()
        result = StateRotator(store).promote({
            'run_id': 'run-1',
            'manifest_key': 'releases/run-1/manifest.json',
            'state_key': 'releases/run-1/teclo-sync.db',
            'state_sha256': 'a' * 64,
        })

        self.assertEqual(result['current']['manifest_key'], 'current/run-1/manifest.json')
        self.assertIsNone(result['previous'])
        self.assertIsNone(result['archived'])
        self.assertIn('current/current.json', store.pointers)
        self.assertNotIn('previous/current.json', store.pointers)

    def test_third_publication_archives_older_previous_and_rotates_snapshots(self):
        store = MemoryStore()
        rotator = StateRotator(store)
        first = {
            'run_id': 'run-1',
            'manifest_key': 'releases/run-1/manifest.json',
            'state_key': 'releases/run-1/teclo-sync.db',
        }
        second = {
            'run_id': 'run-2',
            'manifest_key': 'releases/run-2/manifest.json',
            'state_key': 'releases/run-2/teclo-sync.db',
        }
        third = {
            'run_id': 'run-3',
            'manifest_key': 'releases/run-3/manifest.json',
            'state_key': 'releases/run-3/teclo-sync.db',
        }

        rotator.promote(first)
        rotator.promote(second)
        result = rotator.promote(third)

        self.assertEqual(result['current']['run_id'], 'run-3')
        self.assertEqual(result['previous']['run_id'], 'run-2')
        self.assertEqual(result['archived']['run_id'], 'run-1')
        self.assertIn('archive/run-1/pointer.json', store.pointers)
        self.assertIn(('archive', 'run-1'), store.releases)
        self.assertEqual(
            store.pointers['current/current.json']['run_id'],
            'run-3',
        )
        self.assertEqual(
            store.pointers['previous/current.json']['run_id'],
            'run-2',
        )

    def test_ambiguous_current_pointer_write_rolls_back_both_slots(self):
        store = MemoryStore()
        rotator = StateRotator(store)
        original_previous = {
            'run_id': 'run-1',
            'manifest_key': 'releases/run-1/manifest.json',
            'state_key': 'releases/run-1/teclo-sync.db',
        }
        original_current = {
            'run_id': 'run-2',
            'manifest_key': 'releases/run-2/manifest.json',
            'state_key': 'releases/run-2/teclo-sync.db',
        }
        store.pointers['current/current.json'] = original_current
        store.pointers['previous/current.json'] = original_previous
        store.fail_current_once = True

        with self.assertRaisesRegex(RuntimeError, 'ambiguous_current_write'):
            rotator.promote({
                'run_id': 'run-3',
                'manifest_key': 'releases/run-3/manifest.json',
                'state_key': 'releases/run-3/teclo-sync.db',
            })

        self.assertEqual(store.pointers['current/current.json'], original_current)
        self.assertEqual(store.pointers['previous/current.json'], original_previous)
        self.assertEqual(store.deleted_prefixes, [])


if __name__ == '__main__':
    unittest.main()
