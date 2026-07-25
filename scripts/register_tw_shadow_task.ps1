if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs; exit
}
$a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File D:\TW-Stock-Trading\scripts\run_tw_shadow.ps1"
$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At "09:40"
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "TwShadow0050" -Action $a -Trigger $t -Settings $set -Description "Weekly 0050 refresh + forward shadow row" -Force | Out-Null
"registered=TwShadow0050 weekly=Sat 09:40 at=$(Get-Date -Format o)" | Out-File "D:\TW-Stock-Trading\data\runtime\shadow_runs\task_registered.txt" -Encoding utf8
