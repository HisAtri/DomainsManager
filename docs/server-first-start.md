# First start of the all-in-one server

`domainsmanager-server` initializes a missing local SQLite database before it
starts the API, worker, scheduler, and notifier. This makes a new local project
usable without a separate migration command.

This bootstrap is intentionally limited to a SQLite path that does not yet
exist. Existing SQLite databases and every PostgreSQL deployment still require
the normal explicit `domainsmanager-migrate` deployment step before starting
application components.
