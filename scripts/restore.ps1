# Restore a backup made by scripts\backup.ps1. REPLACES the current database, Chroma data and matching objects.
# Run from the project root:  powershell -ExecutionPolicy Bypass -File scripts\restore.ps1 -From backups\<timestamp>
# Objects not present in the backup are left in the bucket (they are no longer referenced by the database).
# A backup from an older version is upgraded to the current schema by the init service (alembic upgrade head);
# a backup from a newer version than this code is refused before anything is changed.
param([Parameter(Mandatory = $true)][string]$From)
$ErrorActionPreference = 'Stop'

function Invoke-Checked([scriptblock]$Command, [string]$Step) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Restore step failed: $Step (exit $LASTEXITCODE)" }
}

$full = (Resolve-Path $From).Path
foreach ($required in 'postgres.dump', 'chroma.tar.gz', 'objects\manifest.json') {
    if (-not (Test-Path (Join-Path $full $required))) { throw "Missing $required in $full" }
}

Invoke-Checked { docker compose up -d postgres seaweedfs chroma --wait } 'start storage services'
Invoke-Checked { docker compose cp (Join-Path $full 'postgres.dump') postgres:/tmp/rag.dump } 'copy dump'

# Schema version of the backup: the single row of alembic_version in the dump.
$data = Invoke-Checked { docker compose exec -T postgres pg_restore --data-only --table=alembic_version -f - /tmp/rag.dump } 'read schema version'
$revision = $null
$inCopy = $false
foreach ($line in $data) {
    if ($line -like 'COPY *alembic_version*') { $inCopy = $true }
    elseif ($inCopy -and $line -eq '\.') { break }
    elseif ($inCopy -and $line.Trim()) { $revision = $line.Trim() }
}
$migrations = Join-Path $PSScriptRoot '..\migrations\versions\*.py'
if (-not $revision -or -not (Select-String -Path $migrations -Pattern "^revision = '$([regex]::Escape($revision))'" -Quiet)) {
    docker compose exec -T postgres rm -f /tmp/rag.dump | Out-Null
    throw "Backup schema version '$revision' is unknown to this code (backup from a newer version?). Nothing was changed."
}
Write-Host "Backup schema version: $revision"

$step = 'database'
try {
    Invoke-Checked { docker compose stop api worker } 'stop api and worker'
    # One transaction: drop every table (including ones newer than the backup), then load the dump. On any error
    # the database is left exactly as it was. The dump is converted to SQL first so a damaged file fails early.
    # sh single quotes only: Windows PowerShell 5.1 drops embedded double quotes from native arguments.
    Invoke-Checked { docker compose exec -T postgres sh -c 'pg_restore --no-owner -f /tmp/rag.sql /tmp/rag.dump && psql -q -v ON_ERROR_STOP=1 --single-transaction -U $POSTGRES_USER -d $POSTGRES_DB -c ''SET client_min_messages = warning'' -c ''DROP SCHEMA public CASCADE'' -c ''CREATE SCHEMA public'' -f /tmp/rag.sql; status=$?; rm -f /tmp/rag.dump /tmp/rag.sql; exit $status' } 'restore database'

    $step = 'objects'
    Invoke-Checked { docker compose run --rm --no-deps -T -u 0 -v "${full}:/backup:ro" api python -m scripts.objects_transfer import --dir /backup/objects } 'import objects'

    $step = 'chroma'
    Invoke-Checked { docker compose stop chroma } 'stop chroma'
    Invoke-Checked { docker run --rm -u 0 --entrypoint sh -v bai1-rag_chroma_data:/data -v "${full}:/backup:ro" bai1-rag:local -c 'find /data -mindepth 1 -delete && tar -xzf /backup/chroma.tar.gz -C /data' } 'restore chroma'
    $step = $null
} catch {
    if ($step -eq 'database') {
        Write-Warning 'Database restore failed; the database was left unchanged.'
    } else {
        Write-Warning "Restore stopped while restoring $step; the database is already restored. Fix the cause and run the script again."
    }
    throw
} finally {
    # Always bring the stack back: init migrates an older backup to the current schema, then api and worker start.
    docker compose up -d
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'docker compose up -d failed; api and worker may be stopped. Check: docker compose logs init api worker'
    }
}
Write-Host "Restored from $full"
