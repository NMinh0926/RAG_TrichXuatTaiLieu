# Restore a backup made by scripts\backup.ps1. REPLACES the current database, Chroma data and matching objects.
# Run from the project root:  powershell -ExecutionPolicy Bypass -File scripts\restore.ps1 -From backups\<timestamp>
# Objects not present in the backup are left in the bucket (they are no longer referenced by the database).
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
Invoke-Checked { docker compose stop api } 'stop api'

Invoke-Checked { docker compose cp (Join-Path $full 'postgres.dump') postgres:/tmp/rag.dump } 'copy dump'
Invoke-Checked { docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner /tmp/rag.dump; status=$?; rm -f /tmp/rag.dump; exit $status' } 'pg_restore'

Invoke-Checked { docker compose run --rm --no-deps -T -u 0 -v "${full}:/backup:ro" api python -m scripts.objects_transfer import --dir /backup/objects } 'import objects'

Invoke-Checked { docker compose stop chroma } 'stop chroma'
Invoke-Checked { docker run --rm -u 0 --entrypoint sh -v bai1-rag_chroma_data:/data -v "${full}:/backup:ro" bai1-rag:local -c 'find /data -mindepth 1 -delete && tar -xzf /backup/chroma.tar.gz -C /data' } 'restore chroma'

Invoke-Checked { docker compose up -d } 'start stack'
Write-Host "Restored from $full"
