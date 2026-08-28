# RDAP expiration data migration

Migration `c8d9e0f1a2b3` adds source-specific expiration fields to
`managed_domain`:

- `registry_expires_at` retains the registry RDAP `expiration` event;
- `registrar_expires_at` stores the registrar RDAP `registrar expiration` event;
- `expiration_status` and `expiration_checked_at` record the derived lifecycle
  result and its evaluation time;
- `registrar_rdap_url` records the validated registrar RDAP related link.

Existing `expires_at` values are copied into `registry_expires_at` and the
status is initialized to `unknown`. The next scheduled refresh fills the
registrar-derived fields. This deliberately avoids treating a registry-side
automatic renewal as a customer renewal.

An RDAP-formatted HTTP 404 from the registry produces `released`. A registrar
lookup failure or 404 leaves an existing registry domain as `unknown`; it is
never sufficient evidence that the domain was released. Expiration reminders
use only `registrar_expires_at` for `active` and `grace_period` domains.

## Backfill operation

After deploying the migration, run `domainsmanager-expiration-backfill` (use
`--limit` to bound a batch). It queues forced refresh tasks only for non-deleted
domains whose lifecycle remains `unknown`; workers perform the actual RDAP
network lookups. The command is idempotent for the migration generation and
does not alter a domain's lifecycle itself.
