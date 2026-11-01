Data in the formats written before schema versions existed (generated once from the code at that point; lines carry no `schema_version`). `tests/test_migrations.py` loads each of these and must keep passing after any later schema change.
Plan id of the fixture set: `9407891c06ab`.
