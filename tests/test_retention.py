import unittest
from unittest.mock import MagicMock
from teclosync.retention import StateRotator, RetentionError

class RetentionTests(unittest.TestCase):
    def rotator(self):
        store = MagicMock()
        store.object_key = lambda key: f'telclosync/state/{key}'
        store.publish_manifest.return_value = 'published'
        return StateRotator(store), store

    def test_first_publication_has_no_previous(self):
        rotator, store = self.rotator()
        result = rotator.promote({'artifacts': []}, 'run-1')
        self.assertIsNone(result['previous'])
        store.publish_manifest.assert_called_once()

    def test_second_publication_preserves_previous(self):
        rotator, store = self.rotator()
        result = rotator.promote({'artifacts': []}, 'run-2', previous_manifest={'artifacts': []})
        self.assertIsNotNone(result['previous'])
        self.assertEqual(store.publish_manifest.call_count, 2)

    def test_prune_rejects_short_retention(self):
        rotator, _ = self.rotator()
        with self.assertRaises(RetentionError):
            rotator.prune_archive('2026-11-18T00:00:00Z')

if __name__ == '__main__':
    unittest.main()
