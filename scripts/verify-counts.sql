-- Row counts used to compare state across restarts and restores.
SELECT 'users', count(*) FROM users
UNION ALL SELECT 'source_documents', count(*) FROM source_documents
UNION ALL SELECT 'exceptions', count(*) FROM exceptions
UNION ALL SELECT 'exceptions_resolved', count(*) FROM exceptions WHERE status = 'resolved'
UNION ALL SELECT 'exception_events', count(*) FROM exception_events
UNION ALL SELECT 'audit_events', count(*) FROM audit_events
UNION ALL SELECT 'knowledge_chunks', count(*) FROM knowledge_chunks
UNION ALL SELECT 'sync_runs', count(*) FROM sync_runs
UNION ALL SELECT 'alembic_version', count(*) FROM alembic_version;
