<#
Removes what costs money.

  .\scripts\teardown.ps1 -ProjectId YOUR_PROJECT_ID
      deletes the Kubernetes resources (including the load balancer) and
      the cluster. Images and Firestore data are kept.

  .\scripts\teardown.ps1 -ProjectId YOUR_PROJECT_ID -DeleteProject
      deletes the whole Google Cloud project. Nothing is kept.
#>
param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [string]$Region = "us-central1",
    [switch]$DeleteProject
)

$ErrorActionPreference = "Stop"

if ($DeleteProject) {
    gcloud projects delete $ProjectId
    exit $LASTEXITCODE
}

# Delete the namespace first so the load balancer is removed cleanly.
kubectl delete namespace snip --ignore-not-found
gcloud container clusters delete snip --region=$Region --project=$ProjectId --quiet
# The reserved HTTPS address is billed while it is not in use, so release
# it too (this fails harmlessly if HTTPS was never set up). A DNS record
# pointing at it has to be removed by hand.
gcloud compute addresses delete snip-ip --global --project=$ProjectId --quiet
exit 0
