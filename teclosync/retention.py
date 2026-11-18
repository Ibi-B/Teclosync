from datetime import datetime, timezone

class RetentionError(Exception):
    pass

class StateRotator:
    def __init__(self, store):
        self.store = store

    def current_manifest_key(self, run_id):
        return self.store.object_key(f'current/manifest-{run_id}.json')

    def previous_manifest_key(self, run_id):
        return self.store.object_key(f'previous/manifest-{run_id}.json')

    def archive_manifest_key(self, run_id):
        return self.store.object_key(f'archive/manifest-{run_id}.json')

    def promote(self, manifest, run_id, previous_manifest=None):
        previous_key = None
        if previous_manifest:
            previous_key = self.store.publish_manifest(previous_manifest, run_id)
        current_key = self.store.publish_manifest(manifest, run_id)
        return {'current': current_key, 'previous': previous_key}

    def archive_previous(self, previous_manifest, run_id):
        if not previous_manifest:
            return None
        return self.store.publish_manifest(previous_manifest, run_id)

    def prune_archive(self, cutoff_iso):
        try:
            cutoff = datetime.fromisoformat(cutoff_iso.replace('Z', '+00:00'))
        except ValueError as error:
            raise RetentionError('invalid_cutoff') from error
        if (datetime.now(timezone.utc) - cutoff).days < 90:
            raise RetentionError('archive_retention_below_90_days')
        return []
