#Requires -Version 5.1
. (Join-Path $PSScriptRoot 'common.ps1')
$m = Read-Install (Split-Path -Parent $PSScriptRoot) -ServiceMode
Invoke-Managed $m @() -Serve
