<#
Runs the four failure and scaling experiments from the report on the live
cluster and saves what happened into evidence\.

  .\scripts\experiments.ps1 -ProjectId YOUR_PROJECT_ID -Address http://ADDRESS

  1. kill-pod    delete one web pod and one links pod while requests flow
  2. under-load  5 minutes of load; watch the autoscaler add web pods
  3. stats-down  scale stats to zero; links must keep working
  4. links-down  scale links to zero; expect clear 503s, then recovery

Takes about 12 minutes. Run one experiment only with -Only kill-pod, etc.
#>
param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [Parameter(Mandatory = $true)][string]$Address,
    [ValidateSet("all", "kill-pod", "under-load", "stats-down", "links-down")][string]$Only = "all"
)

$Root = Split-Path -Parent $PSScriptRoot
$Out = Join-Path $Root "evidence"
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$Address = $Address.TrimEnd("/")

function Say($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Stamp { Get-Date -Format "HH:mm:ss" }
function Note($file, $text) { Add-Content -Path (Join-Path $Out $file) -Value "$(Stamp)  $text" }
function Snapshot($name) {
    & (Join-Path $PSScriptRoot "evidence.ps1") -ProjectId $ProjectId -Name $name | Out-Null
}
function FirstPod($app) {
    (kubectl -n snip get pods -l "app=$app" -o "jsonpath={.items[0].metadata.name}")
}
function PodCount($app) {
    @(kubectl -n snip get pods -l "app=$app" --field-selector=status.phase=Running -o name).Count
}
function Background($script, $arguments, $outFile) {
    $all = @("-u", (Join-Path $PSScriptRoot $script)) + $arguments
    Start-Process py -ArgumentList $all -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $Out $outFile)
}
function Status($path) {
    curl.exe -s -m 10 -o NUL -w "%{http_code}" "$Address$path"
}
function WaitForPods($app, $count) {
    for ($i = 0; $i -lt 60 -and (PodCount $app) -ne $count; $i++) { Start-Sleep -Seconds 2 }
}

function KillPod {
    Say "1. kill-pod"
    $log = "kill-pod-actions.txt"
    Set-Content -Path (Join-Path $Out $log) -Value "# kill one web pod, then one links pod, while availability.py runs"
    $probe = Background "availability.py" @($Address, "75") "kill-pod-availability.txt"
    Note $log "availability.py started (75 s, 5 requests a second to /api/links)"
    Start-Sleep -Seconds 12
    $web = FirstPod "web"
    kubectl -n snip delete pod $web --wait=false | Out-Null
    Note $log "deleted web pod $web"
    Start-Sleep -Seconds 25
    $links = FirstPod "links"
    kubectl -n snip delete pod $links --wait=false | Out-Null
    Note $log "deleted links pod $links"
    $probe.WaitForExit()
    Note $log "availability.py finished"
    Snapshot "kill-pod"
}

function UnderLoad {
    Say "2. under-load"
    $log = "under-load-timeline.txt"
    Set-Content -Path (Join-Path $Out $log) -Value "# web autoscaler during a 5 minute load test (80 threads)"
    Note $log ("before: " + (kubectl -n snip get hpa web --no-headers))
    $load = Background "load_test.py" @($Address, "300", "80") "under-load-loadtest.txt"
    while (-not $load.HasExited) {
        Start-Sleep -Seconds 30
        Note $log ((kubectl -n snip get hpa web --no-headers) + "   running web pods: " + (PodCount "web"))
    }
    Snapshot "under-load"
    Note $log "load stopped"
}

function ServiceDown($service) {
    Say "$service-down"
    $log = "$service-down-actions.txt"
    Set-Content -Path (Join-Path $Out $log) -Value "# scale $service to zero pods, observe, then restore"
    $code = ((curl.exe -s -m 10 "$Address/api/links") | ConvertFrom-Json).links[0].code
    Note $log "before: /api/status = $(curl.exe -s -m 10 "$Address/api/status")"
    kubectl -n snip scale deployment $service --replicas=0 | Out-Null
    WaitForPods $service 0
    Note $log "$service scaled to 0 pods"
    $probe = Background "availability.py" @($Address, "30") "$service-down-availability.txt"
    Start-Sleep -Seconds 8
    Note $log "/api/status = $(curl.exe -s -m 10 "$Address/api/status")"
    Note $log "GET /            -> $(Status '/')"
    Note $log "GET /readyz      -> $(Status '/readyz')"
    Note $log "GET /api/links   -> $(Status '/api/links')"
    Note $log "GET /r/$code -> $(Status "/r/$code")"
    $probe.WaitForExit()
    Snapshot "$service-down"
    kubectl -n snip scale deployment $service --replicas=2 | Out-Null
    kubectl -n snip rollout status "deployment/$service" --timeout=300s | Out-Null
    Start-Sleep -Seconds 5
    Note $log "$service restored to 2 pods"
    Note $log "after: /api/status = $(curl.exe -s -m 10 "$Address/api/status")"
    Note $log "after: GET /api/links -> $(Status '/api/links')"
}

if ($Only -eq "all" -or $Only -eq "kill-pod")   { KillPod }
if ($Only -eq "all" -or $Only -eq "under-load") { UnderLoad }
if ($Only -eq "all" -or $Only -eq "stats-down") { ServiceDown "stats" }
if ($Only -eq "all" -or $Only -eq "links-down") { ServiceDown "links" }
Say "done"
