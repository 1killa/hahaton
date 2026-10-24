param(
    [string]$ConfigDir = (Join-Path $env:APPDATA 'steslicer\7.10')
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repoRoot 'plugin\AIAssistant'
$destination = Join-Path $ConfigDir 'plugins\AIAssistant'
if (-not (Test-Path -LiteralPath (Join-Path $source 'plugin.json'))) { throw 'Plugin source was not found.' }
if (Test-Path -LiteralPath $destination) {
    throw "Plugin directory already exists: $destination. Close STE and rename the existing directory to preserve it before installing."
}
New-Item -ItemType Directory -Path $destination -Force | Out-Null
Get-ChildItem -LiteralPath $source -Recurse -File | Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and $_.Extension -notin @('.pyc', '.pyo', '.qmlc', '.jsc') } | ForEach-Object {
    $relativePath = $_.FullName.Substring($source.Length + 1)
    $targetPath = Join-Path $destination $relativePath
    New-Item -ItemType Directory -Path (Split-Path -Parent $targetPath) -Force | Out-Null
    Copy-Item -LiteralPath $_.FullName -Destination $targetPath
}
Write-Output "Installed: $destination"
Write-Output 'Restart STE Slicer. Open Extensions > STE Assistant > Open chat and settings.'
