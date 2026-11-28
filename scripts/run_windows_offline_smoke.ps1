$ErrorActionPreference = 'Stop'
$python = (& python -c 'import sys; print(sys.executable)').Trim()
if ($LASTEXITCODE -ne 0 -or -not [IO.Path]::IsPathFullyQualified($python)) {
    throw 'Could not identify the Python executable used by the Windows smoke tests'
}

$profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore)
if ($profiles.Count -ne 3 -or @($profiles | Where-Object {
    $_.Enabled -ne 'True' -or $_.AllowLocalFirewallRules -eq 'False'
}).Count -ne 0 -or @(Get-Service BFE, MpsSvc | Where-Object Status -ne 'Running').Count -ne 0) {
    throw 'Windows network isolation is unavailable'
}

$id = 'prism-offline-' + [guid]::NewGuid().ToString('N')
$guardDir = Join-Path ([IO.Path]::GetTempPath()) $id
$remoteAddresses = @(
    '0.0.0.0-126.255.255.255'
    '128.0.0.0-255.255.255.255'
    '::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff'
)
function Invoke-OfflinePython([string[]] $PythonArguments) {
    $start = [Diagnostics.ProcessStartInfo]::new($python)
    $start.UseShellExecute = $false
    $start.Environment.Clear()
    foreach ($key in 'SystemRoot', 'WINDIR', 'PATH') {
        $start.Environment[$key] = [Environment]::GetEnvironmentVariable($key)
    }
    foreach ($key in 'HOME', 'USERPROFILE', 'TEMP', 'TMP', 'PYTHONPATH') {
        $start.Environment[$key] = $guardDir
    }
    $start.Environment['PYTHONDONTWRITEBYTECODE'] = '1'
    $start.Environment['SIGNAL_FRESH_QUOTE_ENABLED'] = 'false'
    foreach ($argument in $PythonArguments) { $start.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::Start($start)
    try {
        $process.WaitForExit()
        return $process.ExitCode
    } finally {
        $process.Dispose()
    }
}

$created = $false
$testExit = 1
try {
    $null = New-NetFirewallRule -Name $id -DisplayName $id -Program $python `
        -Direction Outbound -Action Block -Enabled True -Profile Any -Protocol Any `
        -RemoteAddress $remoteAddresses
    $created = $true
    $active = Get-NetFirewallRule -Name $id -PolicyStore ActiveStore
    if ($active.Enabled -ne 'True' -or $active.Action -ne 'Block' -or $active.Direction -ne 'Outbound' -or
        ($active | Get-NetFirewallApplicationFilter).Program -ine $python -or
        @((($active | Get-NetFirewallAddressFilter).RemoteAddress)).Count -ne $remoteAddresses.Count) {
        throw 'Test Python egress rule was not activated as requested'
    }

    # Block Python DNS calls before they can delegate to the Windows DNS service.
    # The spawned smoke-test child uses the same executable and Python bootstrap.
    $null = New-Item -ItemType Directory -Path $guardDir
    @'
import ipaddress
import socket
import sys

def numeric_loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False

def offline_dns(event, args):
    if event == "socket.getnameinfo":
        raise PermissionError("Windows offline smoke denies reverse DNS")
    if event not in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex", "socket.gethostbyaddr"}:
        return
    if event == "socket.getaddrinfo" and args[0] is None:
        return
    if event != "socket.gethostbyaddr" and numeric_loopback(args[0]):
        return
    raise PermissionError("Windows offline smoke denies DNS")

def loopback_address(sock, address):
    if sock.family in (socket.AF_INET, socket.AF_INET6):
        if not isinstance(address, tuple) or not address or not numeric_loopback(address[0]):
            raise PermissionError("Windows offline smoke requires a numeric loopback socket address")

def restrict_address(method):
    def guarded(sock, address):
        loopback_address(sock, address)
        return method(sock, address)
    return guarded

original_sendto = socket.socket.sendto

def sendto(sock, data, *args):
    if args:
        loopback_address(sock, args[-1])
    return original_sendto(sock, data, *args)

for name in ("bind", "connect", "connect_ex"):
    setattr(socket.socket, name, restrict_address(getattr(socket.socket, name)))
socket.socket.sendto = sendto
sys.addaudithook(offline_dns)
sys._prism_offline_dns = True
'@ | Set-Content -LiteralPath (Join-Path $guardDir 'sitecustomize.py') -Encoding utf8
    $startupCheck = @'
import asyncio
import socket
import sys

assert sys._prism_offline_dns
try:
    socket.getaddrinfo("offline-guard.invalid", 0, flags=socket.AI_NUMERICHOST)
except PermissionError:
    pass
else:
    raise RuntimeError("Python DNS guard did not deny the hostname")
asyncio.run(asyncio.sleep(0))
'@
    if ((Invoke-OfflinePython @('-c', $startupCheck)) -ne 0) {
        throw 'Windows Python network guard did not pass its startup checks'
    }

    Write-Output 'OFFLINE_WINDOWS_ACTIVE: per-Python non-loopback egress blocked; DNS denied'
    $testExit = Invoke-OfflinePython (@('-m', 'pytest', '-q') + $args)
} finally {
    if ($created) { Remove-NetFirewallRule -Name $id }
    if (Test-Path -LiteralPath $guardDir) { Remove-Item -LiteralPath $guardDir -Recurse -Force }
}
exit $testExit
