<#
.SYNOPSIS
  SENTRIX task runner for Windows (same targets as the Makefile).
.EXAMPLE
  .\make.ps1 help
  .\make.ps1 check
#>
param([string]$Target = "help")

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Python = if (Test-Path "$Root\.venv\Scripts\python.exe") { "$Root\.venv\Scripts\python.exe" } else { "python" }
$Pushgateway = if ($env:SENTRIX_PUSHGATEWAY) { $env:SENTRIX_PUSHGATEWAY } else { "http://localhost:9091" }
$PromImage = "prom/prometheus:v2.55.1"
$RulesDir = Join-Path $Root "monitoring\prometheus"

function Invoke-Step([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "failed ($LASTEXITCODE): $Exe $($Arguments -join ' ')" }
}

function Invoke-Pushing([string[]]$Arguments) {
    $env:SENTRIX_PUSHGATEWAY = $Pushgateway
    Invoke-Step $Python $Arguments
}

$Targets = [ordered]@{
    help      = @{ Desc = "List available targets"; Run = {
                    $Targets.GetEnumerator() | ForEach-Object { "  {0,-10} {1}" -f $_.Key, $_.Value.Desc } } }
    install   = @{ Desc = "Install pinned dependencies (runtime + dev)"; Run = {
                    Invoke-Step $Python @("-m", "pip", "install", "-r", "requirements.txt", "-r", "requirements-dev.txt") } }
    lint      = @{ Desc = "Ruff lint + format check"; Run = {
                    Invoke-Step $Python @("-m", "ruff", "check", ".")
                    Invoke-Step $Python @("-m", "ruff", "format", "--check", ".") } }
    format    = @{ Desc = "Auto-fix lint issues and format the code"; Run = {
                    Invoke-Step $Python @("-m", "ruff", "check", ".", "--fix")
                    Invoke-Step $Python @("-m", "ruff", "format", ".") } }
    test      = @{ Desc = "Unit and integration tests"; Run = { Invoke-Step $Python @("-m", "pytest", "-q") } }
    pipeline  = @{ Desc = "Full pipeline (pushes metrics if the stack is up)"; Run = { Invoke-Pushing @("-m", "src.pipeline") } }
    monitor   = @{ Desc = "Monitoring stage only, over existing batches"; Run = { Invoke-Pushing @("-m", "src.pipeline", "--monitor") } }
    findings  = @{ Desc = "Reproduce the evidence of README > Technical findings"; Run = { Invoke-Step $Python @("-m", "src.technical_findings") } }
    push      = @{ Desc = "Re-send the last run's metrics to the Pushgateway"; Run = { Invoke-Pushing @("-m", "src.push_metrics") } }
    dashboard = @{ Desc = "Regenerate the Grafana dashboard JSON from code"; Run = { Invoke-Step $Python @("monitoring/grafana/generate_dashboard.py") } }
    site      = @{ Desc = "Build the GitHub Pages portal into site/ (needs a pipeline run)"; Run = { Invoke-Step $Python @("-m", "src.portal") } }
    serve     = @{ Desc = "Build and serve the portal at http://127.0.0.1:8765"; Run = {
                    & $Targets["site"].Run
                    Invoke-Step $Python @("-m", "http.server", "8765", "--bind", "127.0.0.1", "-d", "site") } }
    promtool  = @{ Desc = "Validate and unit-test the Prometheus alert rules"; Run = {
                    foreach ($cmd in @(@("check", "rules", "alerts.yml"), @("test", "rules", "alerts_test.yml"))) {
                        Invoke-Step "docker" (@("run", "--rm", "-v", "${RulesDir}:/rules", "-w", "/rules", "--entrypoint", "promtool", $PromImage) + $cmd)
                    } } }
    up        = @{ Desc = "Start Pushgateway + Prometheus + Grafana (Grafana :3100, Prometheus :9190)"; Run = { Invoke-Step "docker" @("compose", "up", "-d") } }
    down      = @{ Desc = "Stop the observability stack (keeps volumes)"; Run = { Invoke-Step "docker" @("compose", "down") } }
    logs      = @{ Desc = "Tail the observability stack logs"; Run = { Invoke-Step "docker" @("compose", "logs", "-f", "--tail=50") } }
    check     = @{ Desc = "Everything CI checks, locally (lint, test, promtool)"; Run = {
                    foreach ($t in "lint", "test", "promtool") { & $Targets[$t].Run } } }
    ci        = @{ Desc = "check + full pipeline"; Run = { & $Targets["check"].Run; & $Targets["pipeline"].Run } }
    clean     = @{ Desc = "Remove caches (keeps data, models, reports)"; Run = {
                    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "$Root\.pytest_cache", "$Root\.ruff_cache", "$Root\site"
                    Get-ChildItem $Root -Recurse -Directory -Filter "__pycache__" |
                        Where-Object { $_.FullName -notlike "*\.venv\*" } |
                        Remove-Item -Recurse -Force } }
}

if (-not $Targets.Contains($Target)) {
    Write-Error "Unknown target '$Target'. Run .\make.ps1 help"
    exit 1
}
Push-Location $Root
try { & $Targets[$Target].Run } finally { Pop-Location }
