param(
    [Parameter(Mandatory = $true)]
    [string]$Name
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$baseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectsDir = Join-Path $baseDir 'mini-projects'
$projectDir = Join-Path $projectsDir $Name
$packageName = ($Name -replace '[^A-Za-z0-9_]', '_').ToLower()

if (Test-Path $projectDir) {
    throw "La carpeta ya existe: $projectDir"
}

New-Item -ItemType Directory -Force $projectDir | Out-Null
New-Item -ItemType Directory -Force (Join-Path $projectDir 'src') | Out-Null
New-Item -ItemType Directory -Force (Join-Path $projectDir "src\\$packageName") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $projectDir 'tests') | Out-Null
New-Item -ItemType Directory -Force (Join-Path $projectDir '.vscode') | Out-Null

$files = @{
    '.gitignore' = @'
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
*.pyc
'@
    'README.md' = @"
# $Name

Mini-proyecto Python creado desde la carpeta base `Code`.
"@
    'requirements.txt' = @'
pytest
ruff
'@
    'src\main.py' = @"
from $packageName.greetings import greet


def main() -> None:
    print(greet())


if __name__ == '__main__':
    main()
"@
    "src\\$packageName\\__init__.py" = @'
'@
    "src\\$packageName\\greetings.py" = @'
def greet() -> str:
    return "Hola desde tu mini-proyecto Python"
'@
    'tests\test_smoke.py' = @"
from $packageName.greetings import greet


def test_greet() -> None:
    assert 'Hola' in greet()
"@
    '.vscode\settings.json' = @'
{
  "python.defaultInterpreterPath": "${workspaceFolder}\\.venv\\Scripts\\python.exe",
  "python.testing.pytestEnabled": true,
  "python.testing.unittestEnabled": false,
  "python.testing.pytestArgs": [
    "tests"
  ],
  "python.analysis.extraPaths": [
    "${workspaceFolder}\\src"
  ],
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff"
  },
  "editor.formatOnSave": true
}
'@
}

foreach ($relativePath in $files.Keys) {
    $targetPath = Join-Path $projectDir $relativePath
    Set-Content -Path $targetPath -Value $files[$relativePath] -Encoding UTF8
}

Push-Location $projectDir
try {
    python -m venv .venv
    & '.\.venv\Scripts\python.exe' -m pip install --upgrade pip
    & '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
}
finally {
    Pop-Location
}

Write-Output "Proyecto creado en: $projectDir"
