from teclosync.external_state import ExternalStateError


class StateRotator:
    """Promote immutable releases through current, previous, and archive prefixes."""

    CURRENT_POINTER = 'current/current.json'
    PREVIOUS_POINTER = 'previous/current.json'

    def __init__(self, store):
        self.store = store

    @staticmethod
    def _source_prefix(pointer):
        if not pointer or not pointer.get('manifest_key'):
            return None
        return pointer['manifest_key'].rsplit('/', 1)[0]

    def _restore_pointer(self, name, pointer):
        if pointer is None:
            self.store.delete_pointer(name)
        else:
            self.store.write_pointer(name, pointer)

    def promote(self, new_release_pointer):
        if not isinstance(new_release_pointer, dict):
            raise ExternalStateError('new_release_pointer_missing')

        current_pointer = self.store.read_current_pointer()
        previous_pointer = self.store.read_previous_pointer()

        # Archive the previous-good release before replacing its protected slot.
        archived_pointer = None
        if previous_pointer:
            archived_pointer = self.store.copy_release(previous_pointer, 'archive')
            self.store.write_pointer(
                f'archive/{archived_pointer["run_id"]}/pointer.json',
                archived_pointer,
            )

        # Prepare complete verified copies before changing either live pointer.
        next_previous_pointer = (
            self.store.copy_release(current_pointer, 'previous')
            if current_pointer
            else None
        )
        next_current_pointer = self.store.copy_release(new_release_pointer, 'current')

        previous_pointer_written = False
        try:
            if next_previous_pointer:
                self.store.write_pointer(self.PREVIOUS_POINTER, next_previous_pointer)
            else:
                self.store.delete_pointer(self.PREVIOUS_POINTER)
            previous_pointer_written = True
            self.store.write_pointer(self.CURRENT_POINTER, next_current_pointer)
        except Exception as publication_error:
            rollback_errors = []
            try:
                self._restore_pointer(self.CURRENT_POINTER, current_pointer)
            except Exception as error:
                rollback_errors.append(f'current:{error}')
            if previous_pointer_written:
                try:
                    self._restore_pointer(self.PREVIOUS_POINTER, previous_pointer)
                except Exception as error:
                    rollback_errors.append(f'previous:{error}')
            if rollback_errors:
                raise ExternalStateError(
                    'release_promotion_failed_and_rollback_failed: '
                    f'{publication_error}; {"; ".join(rollback_errors)}'
                ) from publication_error
            raise

        # Once both pointers are verified, discard only the superseded source
        # copies. The protected current/previous/archive copies remain intact.
        cleanup_errors = []
        for pointer in (new_release_pointer, current_pointer, previous_pointer):
            source_prefix = self._source_prefix(pointer)
            if source_prefix:
                try:
                    self.store.delete_prefix(source_prefix)
                except Exception as error:
                    cleanup_errors.append(f'{source_prefix}:{error}')

        return {
            'current': next_current_pointer,
            'previous': next_previous_pointer,
            'archived': archived_pointer,
            'cleanup_errors': cleanup_errors,
        }
