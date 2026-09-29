# Backup PostgreSQL (pg_dump), every stored object (S3 API) and the Chroma volume into backups\<timestamp>.
# Run from the project root with the stack up:  powershell -ExecutionPolicy Bypass -File scripts\backup.ps1
# The API is stopped for the duration (and Chroma while it is archived) so the database, objects and
# vectors are copied at the same point in time; everything is restarted afterwards, even on failure.
param([string]$Destination = 'backups')
$ErrorActionPreference = 'Stop'

function Invoke-Checked([scriptblock]$Command, [string]$Step) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Backup step failed: $Step (exit $LASTEXITCODE)" }
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$target = Join-Path $Destination $stamp
New-Item -ItemType Directory -Force (Join-Path $target 'objects') | Out-Null
$full = (Resolve-Path $target).Path

Invoke-Checked { docker compose stop api } 'stop api'
try {
    # pg_dump writes inside the container: redirecting binary output through PowerShell would corrupt it.
    Invoke-Checked { docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/rag.dump' } 'pg_dump'
    Invoke-Checked { docker compose cp postgres:/tmp/rag.dump (Join-Path $full 'postgres.dump') } 'copy dump'
    Invoke-Checked { docker compose exec -T postgres rm -f /tmp/rag.dump } 'remove temporary dump'

    Invoke-Checked { docker compose run --rm --no-deps -T -u 0 -v "${full}:/backup" api python -m scripts.objects_transfer export --dir /backup/objects } 'export objects'

    Invoke-Checked { docker compose stop chroma } 'stop chroma'
    try {
        Invoke-Checked { docker run --rm -u 0 --entrypoint tar -v bai1-rag_chroma_data:/data:ro -v "${full}:/backup" bai1-rag:local -czf /backup/chroma.tar.gz -C /data . } 'archive chroma'
    } finally {
        docker compose start chroma | Out-Null
    }
} finally {
    docker compose start api | Out-Null
}
Write-Host "Backup written to $full"
