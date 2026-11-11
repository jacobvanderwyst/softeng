#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess)]
param([switch]$Help, [string]$InstallRoot = 'C:\Program Files\EduNav', [switch]$LeaveStopped)
# Scheduling is opt-in: an administrator may schedule this entry point; it never registers a task.
& (Join-Path $PSScriptRoot 'manage.ps1') -Action Backup -InstallRoot $InstallRoot -Help:$Help -LeaveStopped:$LeaveStopped -WhatIf:$WhatIfPreference
