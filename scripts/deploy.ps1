<#
Deploys Snip to Google Cloud.

  .\scripts\deploy.ps1 -ProjectId YOUR_PROJECT_ID

What it does, in order:
  setup  - turns on the APIs, creates the image registry, the Firestore
           database and the GKE Autopilot cluster, and grants the two IAM
           roles the system needs
  build  - builds the three images and pushes them to Artifact Registry
  apply  - fills in the image names in k8s\*.yaml, applies them, waits for
           the pods, and prints the public address

Run one part only with -Step setup | build | apply.
Add -UseCloudBuild to build the images in Google Cloud instead of with
Docker Desktop on this PC.
Add -Domain snip.example.com -DnsZone my-zone to also serve the site over
HTTPS on that name: reserves an address, points the DNS record at it and
asks Google for a certificate (issuing it takes 15 to 60 minutes).
It is safe to run again: anything that already exists is left alone.
#>
param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [string]$Region = "us-central1",
    [ValidateSet("all", "setup", "build", "apply")][string]$Step = "all",
    [switch]$UseCloudBuild,
    [string]$Tag = "",
    [string]$Domain = "",
    [string]$DnsZone = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Cluster = "snip"
$Repo = "snip"
$Namespace = "snip"
$Services = @("web", "links", "stats")
$Registry = "${Region}-docker.pkg.dev/${ProjectId}/${Repo}"
$TagFile = Join-Path $Root ".last-tag"

function Say($text) {
    Write-Host ""
    Write-Host "== $text" -ForegroundColor Cyan
}

# gcloud, docker and kubectl do not stop the script when they fail, so
# check the exit code after each one.
function Check($what) {
    if ($LASTEXITCODE -ne 0) {
        throw "$what failed (exit code $LASTEXITCODE)"
    }
}

function Setup {
    Say "Project and APIs"
    gcloud config set project $ProjectId
    Check "gcloud config set project"
    gcloud services enable container.googleapis.com artifactregistry.googleapis.com firestore.googleapis.com cloudbuild.googleapis.com
    Check "enabling APIs"

    Say "Artifact Registry repository '$Repo'"
    $repos = gcloud artifacts repositories list --location=$Region --format="value(name)"
    Check "listing repositories"
    if ($repos | Where-Object { $_ -match "(^|/)${Repo}$" }) {
        Write-Host "already exists"
    } else {
        gcloud artifacts repositories create $Repo --repository-format=docker --location=$Region --description="Snip container images"
        Check "creating the repository"
    }

    Say "Firestore database"
    $databases = gcloud firestore databases list --format="value(name)"
    Check "listing Firestore databases"
    if ($databases) {
        Write-Host "already exists"
    } else {
        gcloud firestore databases create --location=nam5 --type=firestore-native
        Check "creating the Firestore database"
    }

    Say "GKE Autopilot cluster '$Cluster' (about 10 minutes the first time)"
    $clusters = gcloud container clusters list --filter="name=$Cluster" --format="value(name)"
    Check "listing clusters"
    if ($clusters) {
        Write-Host "already exists"
    } else {
        gcloud container clusters create-auto $Cluster --region=$Region
        Check "creating the cluster"
    }
    gcloud container clusters get-credentials $Cluster --region=$Region
    Check "getting cluster credentials"

    Say "IAM"
    $number = gcloud projects describe $ProjectId --format="value(projectNumber)"
    Check "reading the project number"

    # Workload Identity: the Kubernetes service account snip-data (used by
    # links and stats) may use Firestore. Nothing else is granted.
    $dataMember = "principal://iam.googleapis.com/projects/${number}/locations/global/workloadIdentityPools/${ProjectId}.svc.id.goog/subject/ns/${Namespace}/sa/snip-data"
    gcloud projects add-iam-policy-binding $ProjectId --member=$dataMember --role=roles/datastore.user --condition=None --format="value(etag)"
    Check "granting Firestore access"

    # The cluster's nodes may pull images from the registry, and have the
    # minimum role GKE asks for (writing logs and metrics). Some projects
    # grant these automatically; granting them again does no harm.
    $nodeMember = "serviceAccount:${number}-compute@developer.gserviceaccount.com"
    foreach ($role in @("roles/artifactregistry.reader", "roles/container.defaultNodeServiceAccount")) {
        gcloud projects add-iam-policy-binding $ProjectId --member=$nodeMember --role=$role --condition=None --format="value(etag)"
        Check "granting $role to the nodes"
    }
}

# Reserve a fixed public address for the HTTPS load balancer and point the
# DNS name at it. The certificate can only be issued once the name resolves
# to this address.
function SetupHttps {
    Say "HTTPS address for $Domain"
    $ip = gcloud compute addresses list --global --filter="name=snip-ip" --format="value(address)"
    Check "listing addresses"
    if (-not $ip) {
        gcloud compute addresses create snip-ip --global
        Check "reserving the address"
        $ip = gcloud compute addresses describe snip-ip --global --format="value(address)"
        Check "reading the address"
    }
    Write-Host "snip-ip = $ip"
    if ($DnsZone) {
        $existing = gcloud dns record-sets list --zone=$DnsZone --name="$Domain." --type=A --format="value(rrdatas)"
        Check "listing DNS records"
        if ($existing) {
            Write-Host "DNS record already exists: $existing"
        } else {
            gcloud dns record-sets create "$Domain." --zone=$DnsZone --type=A --ttl=300 --rrdatas=$ip
            Check "creating the DNS record"
        }
    } else {
        Write-Host "Create an A record for $Domain pointing at $ip at your DNS provider."
    }
}

function Build {
    if (-not $script:Tag) {
        $script:Tag = Get-Date -Format "yyyyMMdd-HHmmss"
    }
    Say "Building images with tag $($script:Tag)"
    if (-not $UseCloudBuild) {
        gcloud auth configure-docker "${Region}-docker.pkg.dev" --quiet
        Check "configuring docker login"
    }
    foreach ($service in $Services) {
        $image = "${Registry}/${service}:$($script:Tag)"
        $folder = Join-Path (Join-Path $Root "services") $service
        Say $image
        if ($UseCloudBuild) {
            gcloud builds submit $folder --tag $image
            Check "cloud build of $service"
        } else {
            docker build -t $image $folder
            Check "docker build of $service"
            docker push $image
            Check "docker push of $service"
        }
    }
    Set-Content -Path $TagFile -Value $script:Tag
}

function Apply {
    if (-not $script:Tag) {
        if (-not (Test-Path $TagFile)) {
            throw "No image tag. Run the build step first or pass -Tag."
        }
        $script:Tag = (Get-Content $TagFile | Select-Object -First 1).Trim()
    }
    Say "Applying manifests with image tag $($script:Tag)"
    gcloud container clusters get-credentials $Cluster --region=$Region --project=$ProjectId
    Check "getting cluster credentials"

    $source = Join-Path $Root "k8s"
    $rendered = Join-Path $source "rendered"
    New-Item -ItemType Directory -Force -Path $rendered | Out-Null
    foreach ($file in Get-ChildItem -Path $source -Filter "*.yaml") {
        $text = [IO.File]::ReadAllText($file.FullName)
        $text = $text.Replace("__REGISTRY__", $Registry).Replace("__TAG__", $script:Tag).Replace("__PROJECT__", $ProjectId)
        [IO.File]::WriteAllText((Join-Path $rendered $file.Name), $text)
    }
    kubectl apply -f $rendered
    Check "kubectl apply"

    foreach ($service in $Services) {
        Say "Waiting for $service"
        kubectl -n $Namespace rollout status "deployment/$service" --timeout=900s
        Check "rollout of $service"
    }

    Say "Waiting for the public address"
    $ip = ""
    for ($i = 0; $i -lt 60 -and -not $ip; $i++) {
        $ip = kubectl -n $Namespace get service web -o "jsonpath={.status.loadBalancer.ingress[0].ip}"
        if (-not $ip) { Start-Sleep -Seconds 5 }
    }
    if (-not $ip) {
        throw "The load balancer has no address yet. Check: kubectl -n $Namespace get service web"
    }
    if ($Domain) {
        Say "HTTPS for $Domain"
        $text = [IO.File]::ReadAllText((Join-Path (Join-Path $source "https") "ingress.yaml"))
        [IO.File]::WriteAllText((Join-Path $rendered "https-ingress.yaml"), $text.Replace("__DOMAIN__", $Domain))
        kubectl apply -f (Join-Path $rendered "https-ingress.yaml")
        Check "kubectl apply (https)"
        Write-Host "https://$Domain will work once the certificate is issued. Check with:"
        Write-Host "  kubectl -n $Namespace get managedcertificate snip-cert"
    }
    Write-Host ""
    Write-Host "Snip is live at http://$ip" -ForegroundColor Green
    Write-Host "Check it with:  py scripts\smoke_test.py http://$ip"
}

$script:Tag = $Tag
if ($Step -eq "all" -or $Step -eq "setup") { Setup }
if ($Domain -and ($Step -eq "all" -or $Step -eq "setup")) { SetupHttps }
if ($Step -eq "all" -or $Step -eq "build") { Build }
if ($Step -eq "all" -or $Step -eq "apply") { Apply }
