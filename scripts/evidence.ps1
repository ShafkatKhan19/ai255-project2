<#
Saves the current state of the deployment into the evidence\ folder, for
the report.

  .\scripts\evidence.ps1 -ProjectId YOUR_PROJECT_ID [-Name after-load]

-Name is added to the file names so several snapshots can be kept
(for example "baseline", "kill-pod", "under-load", "stats-down", "links-down").
#>
param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [string]$Region = "us-central1",
    [string]$Name = "baseline"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Out = Join-Path $Root "evidence"
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$Registry = "${Region}-docker.pkg.dev/${ProjectId}/snip"

function Save($file, [scriptblock]$command) {
    $path = Join-Path $Out "${Name}-${file}.txt"
    $stamp = "# captured " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    $lines = & $command
    @($stamp) + $lines | Out-File -FilePath $path -Encoding utf8
    Write-Host "wrote $path"
}

Save "pods"        { kubectl -n snip get pods -o wide }
Save "deployments" { kubectl -n snip get deployments -o wide }
Save "services"    { kubectl -n snip get services -o wide }
Save "hpa"         { kubectl -n snip get hpa }
Save "hpa-detail"  { kubectl -n snip describe hpa }
Save "pdb-netpol"  { kubectl -n snip get poddisruptionbudgets,networkpolicies }
Save "probes"      { kubectl -n snip describe deployments | Select-String -Pattern "^Name:|Liveness|Readiness|Image:" | ForEach-Object { $_.Line.Trim() } }
Save "top"         { kubectl -n snip top pods }
Save "events"      { kubectl -n snip get events --sort-by=.lastTimestamp }
Save "images"      { gcloud artifacts docker images list $Registry --include-tags --format="table(package,tags,createTime)" }
