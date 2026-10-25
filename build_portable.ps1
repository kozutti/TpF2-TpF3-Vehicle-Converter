param(
    [string]$Python = "",
    [switch]$InstallDependencies,
    [string]$ReleaseDirectory = ""
)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
Set-Location -LiteralPath $taskRoot
if (-not $Python) {
    $Python = Join-Path $taskRoot '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Python not found. Create .venv with Python 3.12+ or specify -Python.'
}
if ($InstallDependencies) {
    & $Python -m pip install --retries 0 -r (Join-Path $taskRoot 'requirements-build.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
& $Python -c 'import tkinter, tkinterdnd2, PyInstaller'
if ($LASTEXITCODE -ne 0) { throw 'Build dependencies missing. Use -InstallDependencies.' }
$releaseRoot = if ($ReleaseDirectory) { [IO.Path]::GetFullPath($ReleaseDirectory) } else { Join-Path $taskRoot 'release' }
$workRoot = Join-Path $taskRoot '.build'
$commonArgs = @(
    '--noconfirm', '--clean', '--onedir',
    '--distpath', $releaseRoot, '--workpath', (Join-Path $workRoot 'pyinstaller'),
    '--specpath', $workRoot, '--collect-all', 'tkinterdnd2', '--collect-all', 'wasmtime', '--collect-all', 'lupa',
    '--add-data', ((Join-Path $taskRoot 'converter\vendor') + ';converter/vendor'),
    '--hidden-import', 'converter.pipeline', '--hidden-import', 'converter.gui.app'
)
if (Test-Path -LiteralPath (Join-Path $taskRoot 'profiles')) {
    $commonArgs += @('--add-data', ((Join-Path $taskRoot 'profiles') + ';profiles'))
}
& $Python -m PyInstaller @commonArgs --windowed --name TPF3VehicleConverter (Join-Path $taskRoot 'launcher.py')
if ($LASTEXITCODE -ne 0) { throw 'GUI EXE build failed.' }
# The second entry point shares the bundled runtime so CLI stdout/stderr work normally.
& $Python -m PyInstaller @commonArgs --console --name TPF3VehicleConverterCLI (Join-Path $taskRoot 'launcher.py')
if ($LASTEXITCODE -ne 0) { throw 'CLI EXE build failed.' }
$portableRoot = Join-Path $releaseRoot 'TPF3VehicleConverter'
Copy-Item -LiteralPath (Join-Path $releaseRoot 'TPF3VehicleConverterCLI\TPF3VehicleConverterCLI.exe') -Destination $portableRoot -Force
Copy-Item -LiteralPath (Join-Path $taskRoot 'README.md') -Destination $portableRoot -Force
foreach ($optional in 'GAME_CHECKLIST.md', 'LICENSE') {
    if (Test-Path -LiteralPath (Join-Path $taskRoot $optional)) {
        Copy-Item -LiteralPath (Join-Path $taskRoot $optional) -Destination $portableRoot -Force
    }
}
if (Test-Path -LiteralPath (Join-Path $taskRoot 'docs')) {
    Copy-Item -LiteralPath (Join-Path $taskRoot 'docs') -Destination $portableRoot -Recurse -Force
}
& $Python -m pip freeze | Set-Content -LiteralPath (Join-Path $portableRoot 'build-dependencies.txt') -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw 'Build dependency recording failed.' }
& $Python (Join-Path $taskRoot 'tools\package_release.py') $releaseRoot
if ($LASTEXITCODE -ne 0) { throw 'Release documentation packaging failed.' }
$archivePath = Join-Path $releaseRoot 'TPF3VehicleConverter-Windows-x64.zip'
Compress-Archive -LiteralPath $portableRoot -DestinationPath $archivePath -Force
Get-Item -LiteralPath $archivePath | Select-Object FullName, Length
Get-FileHash -LiteralPath $archivePath -Algorithm SHA256
