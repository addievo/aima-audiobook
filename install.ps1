# aima-audiobook one-line installer for Windows (PowerShell):
#   irm https://raw.githubusercontent.com/addievo/aima-audiobook/main/install.ps1 | iex
# Installs uv (if missing), the aima-audiobook command, and ffmpeg via winget (if missing).
$ErrorActionPreference = "Stop"
$src = "git+https://github.com/addievo/aima-audiobook"
if ($env:AIMA_AUDIOBOOK_SRC) { $src = $env:AIMA_AUDIOBOOK_SRC }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "== installing uv"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

if (($src -like "git+*") -and -not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "== installing git"
    winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
    $env:Path = "$env:ProgramFiles\Git\cmd;$env:Path"
}

Write-Host "== installing aima-audiobook"
uv tool install --force --python 3.12 $src

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "== installing ffmpeg"
    winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
}

try { uv tool update-shell | Out-Null } catch {}
Write-Host ""
Write-Host "done. Open a new terminal, then:"
Write-Host "  aima-audiobook toc your-book.pdf"
Write-Host "  aima-audiobook build your-book.pdf --select rmit-ai26"
