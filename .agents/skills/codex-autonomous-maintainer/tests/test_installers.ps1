[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Temp = Join-Path ([IO.Path]::GetTempPath()) ('autonomous-maintainer-tests-' + [Guid]::NewGuid().ToString('N'))

function Invoke-PwshFile {
    param(
        [Parameter(Mandatory)]
        [string]$File,
        [string[]]$ScriptArgs = @(),
        [switch]$ExpectFailure
    )

    & pwsh -NoLogo -NoProfile -NonInteractive -File $File @ScriptArgs
    $ExitCode = $LASTEXITCODE
    if ($ExpectFailure) {
        if ($ExitCode -eq 0) { throw "Expected failure from $File" }
    } elseif ($ExitCode -ne 0) {
        throw "$File failed with exit code $ExitCode"
    }
}

function Assert-FileEqual {
    param(
        [Parameter(Mandatory)]
        [string]$Expected,
        [Parameter(Mandatory)]
        [string]$Actual
    )

    if (-not (Test-Path -LiteralPath $Actual -PathType Leaf)) {
        throw "Expected file was not created: $Actual"
    }
    $ExpectedHash = (Get-FileHash -LiteralPath $Expected -Algorithm SHA256).Hash
    $ActualHash = (Get-FileHash -LiteralPath $Actual -Algorithm SHA256).Hash
    if ($ExpectedHash -ne $ActualHash) {
        throw "File content mismatch: $Actual"
    }
}

try {
    $HomeDir = Join-Path $Temp 'home'
    $env:HOME = $HomeDir
    $env:USERPROFILE = $HomeDir
    $env:CODEX_HOME = Join-Path $HomeDir '.codex'
    New-Item -ItemType Directory -Path $HomeDir -Force | Out-Null

    $Install = Join-Path $Root 'install.ps1'
    $Uninstall = Join-Path $Root 'uninstall.ps1'
    $OmxSource = Join-Path $Root 'SKILL.md'
    $StandaloneSource = Join-Path (Join-Path $Root 'standalone') 'SKILL.md'

    Invoke-PwshFile -File $Install -ScriptArgs @('-Variant', 'invalid') -ExpectFailure

    Invoke-PwshFile -File $Install -ScriptArgs @('-Variant', 'omx', '-Scope', 'user')
    $OmxUserFile = Join-Path $env:CODEX_HOME 'skills/autonomous-maintainer/SKILL.md'
    Assert-FileEqual -Expected $OmxSource -Actual $OmxUserFile
    Invoke-PwshFile -File $Install -ScriptArgs @('-Variant', 'omx', '-Scope', 'user')

    Invoke-PwshFile -File $Install -ScriptArgs @('-Variant', 'standalone', '-Scope', 'user')
    $StandaloneUserFile = Join-Path $env:CODEX_HOME 'skills/autonomous-maintainer-standalone/SKILL.md'
    Assert-FileEqual -Expected $StandaloneSource -Actual $StandaloneUserFile
    Invoke-PwshFile -File $Install -ScriptArgs @('-Variant', 'standalone', '-Scope', 'user')

    Add-Content -LiteralPath $OmxUserFile -Value "`n# local modification"
    Invoke-PwshFile -File $Install -ScriptArgs @('-Variant', 'omx', '-Scope', 'user') -ExpectFailure
    Invoke-PwshFile -File $Install -ScriptArgs @('-Variant', 'omx', '-Scope', 'user', '-Force')
    Assert-FileEqual -Expected $OmxSource -Actual $OmxUserFile
    $Backups = Get-ChildItem -LiteralPath (Split-Path -Parent $OmxUserFile) -Filter 'SKILL.md.backup-*'
    if (-not $Backups) { throw 'Expected forced installation to create a backup' }

    $DryProject = Join-Path $Temp 'dry-project'
    New-Item -ItemType Directory -Path $DryProject -Force | Out-Null
    Invoke-PwshFile -File $Install -ScriptArgs @(
        '-Variant', 'standalone', '-Scope', 'project', '-ProjectDir', $DryProject, '-DryRun'
    )
    $DryTarget = Join-Path $DryProject '.codex/skills/autonomous-maintainer-standalone/SKILL.md'
    if (Test-Path -LiteralPath $DryTarget) { throw 'Dry-run unexpectedly created a file' }

    $Project = Join-Path $Temp 'project'
    New-Item -ItemType Directory -Path $Project -Force | Out-Null
    Invoke-PwshFile -File $Install -ScriptArgs @(
        '-Variant', 'omx', '-Scope', 'project', '-ProjectDir', $Project
    )
    $OmxProjectFile = Join-Path $Project '.codex/skills/autonomous-maintainer/SKILL.md'
    Assert-FileEqual -Expected $OmxSource -Actual $OmxProjectFile

    Invoke-PwshFile -File $Install -ScriptArgs @(
        '-Variant', 'standalone', '-Scope', 'project', '-ProjectDir', $Project
    )
    $StandaloneProjectFile = Join-Path $Project '.codex/skills/autonomous-maintainer-standalone/SKILL.md'
    Assert-FileEqual -Expected $StandaloneSource -Actual $StandaloneProjectFile

    Invoke-PwshFile -File $Uninstall -ScriptArgs @(
        '-Variant', 'standalone', '-Scope', 'project', '-ProjectDir', $Project, '-Confirm:$false'
    )
    if (Test-Path -LiteralPath $StandaloneProjectFile) { throw 'Standalone project uninstall failed' }

    Invoke-PwshFile -File $Uninstall -ScriptArgs @(
        '-Variant', 'omx', '-Scope', 'project', '-ProjectDir', $Project, '-Confirm:$false'
    )
    if (Test-Path -LiteralPath $OmxProjectFile) { throw 'OMX project uninstall failed' }

    Invoke-PwshFile -File $Uninstall -ScriptArgs @(
        '-Variant', 'standalone', '-Scope', 'user', '-Confirm:$false'
    )
    if (Test-Path -LiteralPath $StandaloneUserFile) { throw 'Standalone user uninstall failed' }

    Invoke-PwshFile -File $Uninstall -ScriptArgs @(
        '-Variant', 'omx', '-Scope', 'user', '-Confirm:$false'
    )
    if (Test-Path -LiteralPath $OmxUserFile) { throw 'OMX user uninstall failed' }

    Write-Host 'ok: PowerShell installer smoke tests passed'
} finally {
    if (Test-Path -LiteralPath $Temp) {
        Remove-Item -LiteralPath $Temp -Recurse -Force
    }
}
