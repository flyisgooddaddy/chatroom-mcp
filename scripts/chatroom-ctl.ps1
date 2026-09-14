# chatroom-ctl.ps1 - manage chatroom-mcp server + openwriter agent cleanly.
# Usage:  .\scripts\chatroom-ctl.ps1 [kill|start|restart|status]
param([Parameter(Position=0)][string]$Cmd = "status")

$CommsDir = "C:\dev\test-chat"
$Repo = "C:\dev\chatroom-mcp"
$Py = "C:\Users\波波\Desktop\openwriter-dev\.venv\Scripts\python.exe"

function Get-ChatroomProcs {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'chatroom\.server|openwriter_agent|bobo_bridge|bobo_agent|callback_receiver' }
}

function Kill-All {
    $procs = Get-ChatroomProcs
    if (-not $procs) { Write-Host "no chatroom processes running"; return }
    foreach ($p in $procs) {
        Write-Host "killing PID $($p.ProcessId)"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
}

function Start-All {
    Kill-All
    $env:PYTHONPATH = "src"
    Write-Host "starting server on 7777..."
    Start-Process -FilePath $Py -ArgumentList "-m","chatroom.server","--comms-dir",$CommsDir,"--port","7777" `
        -WorkingDirectory $Repo -WindowStyle Hidden
    Start-Sleep -Seconds 3
    Write-Host "starting openwriter agent on 9000..."
    Start-Process -FilePath $Py -ArgumentList "-u","-m","scripts.openwriter_agent","--comms-dir",$CommsDir,"--port","9000" `
        -WorkingDirectory $Repo -WindowStyle Hidden
    Start-Sleep -Seconds 2
    Show-Status
}

function Show-Status {
    Write-Host "`n--- listening ports ---"
    Get-NetTCPConnection -LocalPort 7777,9000 -State Listen -ErrorAction SilentlyContinue |
        Select-Object LocalPort, OwningProcess | Format-Table -AutoSize
    Write-Host "--- chatroom processes ---"
    Get-ChatroomProcs | Select-Object ProcessId, @{n='cmd';e={$_.CommandLine.Substring(0, [Math]::Min(90, $_.CommandLine.Length))}} |
        Format-Table -Wrap
}

switch ($Cmd) {
    "kill"    { Kill-All; Show-Status }
    "start"   { Start-All }
    "restart" { Start-All }
    "status"  { Show-Status }
    default   { Write-Host "usage: chatroom-ctl.ps1 [kill|start|restart|status]" }
}
