# Add a database migration

Every schema change is a numbered file in
[`migrations/versions/`](../../migrations/versions/), applied by Alembic in
order. Why Alembic: [components → migrations](../reference/components.md#migrations--database-schema).

## Steps

1. **Create the file** `migrations/versions/0003_<what>.py`, copying the
   header of [`0002_conversations.py`](../../migrations/versions/0002_conversations.py):

   ```python
   revision = "0003"
   down_revision = "0002"

   def upgrade():
       op.execute("""ALTER TABLE conversations ADD COLUMN context jsonb NOT NULL DEFAULT '{}';""")

   def downgrade():
       op.execute("ALTER TABLE conversations DROP COLUMN context;")
   ```

   Use plain SQL, like the existing migrations.
2. **Backfill existing rows** in the same `upgrade()` if the new column or
   table needs data. 0002 shows how: it builds customers and conversations
   from 0001's rows.
3. **Write a real `downgrade()`.** Say in a comment what it loses, as 0002
   does.
4. **Update the code** that reads and writes the table, usually
   [`care/state.py`](../../care/state.py): the dataclass, the `_CONVERSATION`
   or `_OUTCOME` select lists, and the writes.
5. **Update [`tests/conftest.py`](../../tests/conftest.py)** if you added a
   table. It goes in the `TRUNCATE` list.
6. **Test the migration on data.** Copy `test_migration_backfills_0001_data`
   in [`test_api.py`](../../tests/test_api.py): migrate down to the previous
   revision, insert old-shape rows, migrate up, and check them.
7. **Update [data model](../reference/data-model.md)** and, if it changes
   behaviour, [architecture](../reference/architecture.md).

## Check it

```bash
export CARE_DATABASE_URL=postgresql://…/care      # a dev database, not production
alembic upgrade head && alembic downgrade -1 && alembic upgrade head
alembic current                                    # 0003 (head)
CARE_TEST_DATABASE_URL=postgresql://…/care_test pytest tests/test_api.py
```

`start.yml` runs `alembic upgrade head` on every start, so a merged migration
is applied the next time the stack starts.
