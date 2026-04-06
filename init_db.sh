#!/bin/bash
# Initialize the BloomHub database and tenants

echo "Starting database initialization..."

# 1. Migrate shared schemas (tenants, domains, etc.)
echo "Migrating shared schemas..."
python manage.py migrate_schemas --shared

# 2. Set up the public tenant (localhost, 127.0.0.1)
echo "Setting up public tenant..."
python manage.py setup_public_tenant

# 3. Migrate all schemas (including public and any other tenants)
echo "Migrating all schemas..."
python manage.py migrate_schemas

# 4. Load permissions from CSV
echo "Loading permissions..."
python manage.py load_permissions permissions.csv

echo "Database initialization complete!"
