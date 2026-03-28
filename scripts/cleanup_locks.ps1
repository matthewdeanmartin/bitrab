# Kill bitrab and uv processes
Stop-Process -Name bitrab, uv -Force -ErrorAction SilentlyContinue

# Kill any python processes running from this project's virtual environment
$venvPath = Join-Path (Get-Location) ".venv"
Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$venvPath*" } | Stop-Process -Force

Write-Host "Cleanup complete. Process locks should be released."
