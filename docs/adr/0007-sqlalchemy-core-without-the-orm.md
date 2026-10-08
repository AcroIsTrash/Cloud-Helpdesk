# Database access uses SQLAlchemy Core with declared tables, not the ORM

The raw `sqlite3` queries from `helpdesk` move to SQLAlchemy Core. Tables are declared in Python (`Table(...)`), so Alembic can generate migrations from them. Queries are written in the expression language, one explicit statement each. There are no ORM classes, sessions or lazy loading: the service layer owns the business rules, and an ORM's unit of work would hide when and what it writes. That matters because each audit Event has to commit in the same transaction as the change it records.

Raw SQL through `text()` was the other option. It was rejected because Alembic can't generate migrations from it.
