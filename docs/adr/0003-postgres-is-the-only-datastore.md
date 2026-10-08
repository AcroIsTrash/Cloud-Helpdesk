# PostgreSQL with pgvector is the only datastore

Tickets, the audit trail (`events`) and ticket embeddings all live in one PostgreSQL database on RDS. SQLite can't be shared safely by several Fargate tasks. DynamoDB fits the relational queue and SLA queries poorly. A separate vector store (Pinecone, OpenSearch) would add a second backup, a second bill, and a sync problem between ticket rows and their vectors. With one database, an audit event commits in the same transaction as the change it records, and similarity search can join straight onto tickets and events.
