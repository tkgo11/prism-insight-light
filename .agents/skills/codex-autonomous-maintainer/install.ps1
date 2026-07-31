[CmdletBinding()]
param(
    [ValidateSet('omx', 'standalone')]
    [string]$Variant = 'omx',
    [ValidateSet('user', 'project')]
    [string]$Scope = 'user',
    [string]$ProjectDir = '',
    [switch]$Force,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Variant -eq 'standalone') {
    $SkillName = 'autonomous-maintainer-standalone'
    $SourceFile = Join-Path (Join-Path $ScriptDir 'standalone') 'SKILL.md'
} else {
    $SkillName = 'autonomous-maintainer'
    $SourceFile = Join-Path $ScriptDir 'SKILL.md'
}

if (-not (Test-Path -LiteralPath $SourceFile -PathType Leaf)) {
    throw "Missing source file: $SourceFile"
}

$Python = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $Python) { $Python = Get-Command python -ErrorAction SilentlyContinue }
if ($Python) {
    & $Python.Source (Join-Path $ScriptDir 'scripts/validate_skill.py') $SourceFile
    if ($LASTEXITCODE -ne 0) { throw 'SKILL.md validation failed' }
} else {
    Write-Warning 'Python 3 was not found; skipping structural validation.'
}

if ($Scope -eq 'user') {
    $CodexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME '.codex' }
    $TargetRoot = Join-Path $CodexRoot 'skills'
} else {
    if ([string]::IsNullOrWhiteSpace($ProjectDir)) { $ProjectDir = (Get-Location).Path }
    $ResolvedProject = (Resolve-Path -LiteralPath $ProjectDir).Path
    $TargetRoot = Join-Path $ResolvedProject '.codex/skills'
}

$TargetDir = Join-Path $TargetRoot $SkillName
$TargetFile = Join-Path $TargetDir 'SKILL.md'

foreach ($Candidate in @($TargetDir, $TargetFile)) {
    if (Test-Path -LiteralPath $Candidate) {
        $Item = Get-Item -LiteralPath $Candidate -Force
        if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Refusing to install through a symbolic-link/reparse-point destination: $Candidate"
        }
    }
}

Write-Host "variant:     $Variant"
Write-Host "scope:       $Scope"
Write-Host "source:      $SourceFile"
Write-Host "destination: $TargetFile"

if (Test-Path -LiteralPath $TargetFile -PathType Leaf) {
    $SourceHash = (Get-FileHash -LiteralPath $SourceFile -Algorithm SHA256).Hash
    $TargetHash = (Get-FileHash -LiteralPath $TargetFile -Algorithm SHA256).Hash
    if ($SourceHash -eq $TargetHash) {
        Write-Host 'already installed and up to date'
        exit 0
    }
    if (-not $Force) {
        throw 'A different installation already exists. Rerun with -Force to back it up and replace it.'
    }
}

if ($DryRun) {
    if (Test-Path -LiteralPath $TargetFile -PathType Leaf) {
        Write-Host 'dry-run: would back up and replace the existing SKILL.md'
    } else {
        Write-Host 'dry-run: would create the skill directory and install SKILL.md'
    }
    exit 0
}

New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null

if (Test-Path -LiteralPath $TargetFile -PathType Leaf) {
    $Timestamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $Backup = "$TargetFile.backup-$Timestamp-$PID"
    Copy-Item -LiteralPath $TargetFile -Destination $Backup
    Write-Host "backup:      $Backup"
}

$TempFile = Join-Path $TargetDir ('.SKILL.md.tmp.' + [Guid]::NewGuid().ToString('N'))
try {
    Copy-Item -LiteralPath $SourceFile -Destination $TempFile
    Move-Item -LiteralPath $TempFile -Destination $TargetFile -Force
} finally {
    if (Test-Path -LiteralPath $TempFile) { Remove-Item -LiteralPath $TempFile -Force }
}

$InstalledHash = (Get-FileHash -LiteralPath $TargetFile -Algorithm SHA256).Hash
$SourceHash = (Get-FileHash -LiteralPath $SourceFile -Algorithm SHA256).Hash
if ($InstalledHash -ne $SourceHash) { throw 'Post-install verification failed.' }

Write-Host "installed:   $TargetFile"
Write-Host 'next: start a new Codex session and inspect available skills'
