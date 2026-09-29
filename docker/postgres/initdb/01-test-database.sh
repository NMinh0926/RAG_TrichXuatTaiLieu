#!/bin/sh
# Separate database for automated tests: <POSTGRES_DB>_test. Runs only on first initialization.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -c "CREATE DATABASE \"${POSTGRES_DB}_test\" OWNER \"$POSTGRES_USER\""
