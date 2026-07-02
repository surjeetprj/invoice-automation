$ErrorActionPreference = "Stop"
$env:PYTHONNOUSERSITE = "1"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Starting BahiAI Package & Build process " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

Write-Host "[1/5] Checking PyInstaller installation..." -ForegroundColor Yellow
if (!(Test-Path ".\.venv\Scripts\pyinstaller.exe")) {
    Write-Host "PyInstaller not found in .venv. Installing it..." -ForegroundColor Cyan
    & ".\.venv\Scripts\python.exe" -m pip install pyinstaller
} else {
    Write-Host "PyInstaller is already installed." -ForegroundColor Green
}

Write-Host "[2/5] Checking Inno Setup compiler (ISCC)..." -ForegroundColor Yellow
$isccPath = $null
$programFilesX86 = [Environment]::GetFolderPath("ProgramFilesX86")
$searchPaths = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$programFilesX86\Inno Setup 6\ISCC.exe"
)
foreach ($path in $searchPaths) {
    if ($path -and (Test-Path $path)) {
        $isccPath = $path
        break
    }
}
if ($null -eq $isccPath) {
    $cmdCheck = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($null -ne $cmdCheck) { $isccPath = $cmdCheck.Source }
}
if ($null -eq $isccPath) {
    Write-Host "Inno Setup compiler (ISCC) was not found. Attempting install..." -ForegroundColor DarkYellow
    try {
        & winget install jrsoftware.InnoSetup --silent --accept-source-agreements --accept-package-agreements
        Start-Sleep -Seconds 15
        foreach ($path in $searchPaths) {
            if ($path -and (Test-Path $path)) { $isccPath = $path; break }
        }
    } catch {
        Write-Warning "winget installation failed."
    }
}
if ($null -eq $isccPath) {
    throw "Inno Setup compiler (ISCC.exe) is required to build the installer."
}
Write-Host "Found Inno Setup Compiler at: $isccPath" -ForegroundColor Green

Write-Host "[3/5] Generating brand icon.ico..." -ForegroundColor Yellow
& ".\.venv\Scripts\python.exe" "desktop_app\resources\generate_icon.py"

Write-Host "[4/5] Running PyInstaller..." -ForegroundColor Yellow
if (Test-Path "build") {
    Write-Host "Cleaning build directory..." -ForegroundColor Cyan
    Remove-Item -Recurse -Force "build"
}
if (Test-Path "dist\BahiAI") {
    Write-Host "Cleaning dist\BahiAI directory..." -ForegroundColor Cyan
    Remove-Item -Recurse -Force "dist\BahiAI"
}
& ".\.venv\Scripts\pyinstaller.exe" --clean --noconfirm "Z-compile\BahiAI.spec"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE. Check the output above for details."
}
Write-Host "PyInstaller build complete." -ForegroundColor Green

Write-Host "[5/5] Compiling installer using Inno Setup..." -ForegroundColor Yellow
& $isccPath "Z-compile\BahiAI.iss"
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup Compiler failed with exit code $LASTEXITCODE. Check the output above for details."
}

Write-Host "==========================================" -ForegroundColor Green
Write-Host " BahiAI Build Completed Successfully!    " -ForegroundColor Green
Write-Host " Shareable Installer: dist\BahiAI.exe     " -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
