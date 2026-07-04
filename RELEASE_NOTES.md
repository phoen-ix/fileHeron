# file:Heron v1.57.0

**Config backup / restore integrity.** First of several follow-up releases working
through the deeper findings from the code audit. This one makes the admin
**configuration backup and restore** round-trip faithfully and stop corrupting a
disaster restore. No database migration and no host step - deploy from this banner.

## What's fixed

- **Email-template customizations now survive a restore.** Your admin-authored
  email templates were being wiped when you imported a settings/branding backup and
  never brought back (they weren't included in the export). They're now exported and
  restored with the rest of your branding.
- **Client/employee connections are restored.** After a full restore, clients and
  employees could no longer see each other as recipients until some unrelated change
  rebuilt the links. Backups now carry the "invited" connections and rebuild the
  shared-group ones automatically on import.
- **Activity logs no longer break a restore.** If you included the logs in a backup,
  a download-history row that pointed at a file/share (which a *config* backup
  deliberately doesn't contain) would abort the whole import - after the point where
  active shares had already been invalidated. Those unresolvable log rows are now
  skipped cleanly, so the restore completes.
- **Restoring no longer leaks storage.** When a restore removes a user who isn't in
  the backup, that user's uploaded files are now properly deleted from storage
  instead of leaving orphaned bytes on disk that nothing could reclaim.

## Notes

- **No migration, no host step** - the in-app Update swaps the backend, worker, and
  frontend images.
- Internal: the test suite now runs config-restore tests with database foreign-key
  enforcement on (matching production), which is what surfaced these.
