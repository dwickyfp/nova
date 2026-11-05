#!/bin/sh
# Seed the Nova Catalog acceptance source (compose profile `catalog-test`).
# Runs once, from /docker-entrypoint-initdb.d, when the volume is empty.
# `nova_reader` is the read-only account a Nova connection uses; its password
# comes from CATALOG_TEST_READER_PASSWORD and is entered through Nova's
# write-only credential flow, never stored in a file.
set -eu
psql -v ON_ERROR_STOP=1 -v reader_password="$CATALOG_TEST_READER_PASSWORD" \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE TABLE public.orders (
    id             INT PRIMARY KEY,
    city           TEXT NOT NULL,
    amount         NUMERIC(12, 2) NOT NULL,
    customer_email TEXT NOT NULL
);
INSERT INTO public.orders VALUES
    (1, 'Jakarta', 120.00, 'ani@example.com'),
    (2, 'Jakarta', 80.50, 'budi@example.com'),
    (3, 'Bandung', 99.90, 'citra@example.com'),
    (4, 'Bandung', 15.00, 'dodi@example.com'),
    (5, 'Surabaya', 42.00, 'eka@example.com');
CREATE TABLE public.customers (id INT PRIMARY KEY, name TEXT NOT NULL);
INSERT INTO public.customers VALUES (1, 'Ani'), (2, 'Budi');
CREATE ROLE nova_reader LOGIN PASSWORD :'reader_password';
GRANT USAGE ON SCHEMA public TO nova_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO nova_reader;
SQL
