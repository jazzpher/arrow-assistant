# Local build on Windows (CI does the same in .github/workflows/installer.yml).
# Needs 64-bit Python 3.12 and Inno Setup 6. Output: dist\ArrowSetup-<version>.exe
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot)
py -3.12 -m venv .build-venv
.\.build-venv\Scripts\python.exe -m pip install --upgrade pip
.\.build-venv\Scripts\python.exe -m pip install -r requirements.txt "pyinstaller>=6.10"
Push-Location packaging
..\.build-venv\Scripts\pyinstaller.exe arrow.spec --noconfirm --distpath ..\dist --workpath ..\build
Pop-Location
$p = Start-Process dist\Arrow\Arrow.exe -ArgumentList "--self-test","selftest.txt" -Wait -PassThru
Get-Content selftest.txt
if ($p.ExitCode -ne 0) { throw "self-test failed" }
$version = (.\.build-venv\Scripts\python.exe -c "import arrow_assistant; print(arrow_assistant.__version__)")
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" "/DAppVersion=$version" packaging\installer.iss
