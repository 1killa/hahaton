param(
    [Parameter(Mandatory = $true)][string]$Script,
    [string]$InstallDir = 'C:\Program Files\Stereotech STE Slicer 7.10',
    [string]$Log = '.runtime\host-python.log'
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = (Resolve-Path -LiteralPath $Script).Path
$logPath = [System.IO.Path]::GetFullPath((Join-Path $taskRoot $Log))
New-Item -ItemType Directory -Path (Split-Path -Parent $logPath) -Force | Out-Null
$dllPath = Join-Path $InstallDir 'python35.dll'
if (-not (Test-Path -LiteralPath $dllPath)) { throw 'The STE Python 3.5 runtime was not found.' }
$dllLiteral = $dllPath.Replace('\', '\\').Replace('"', '\"')
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class STEPythonRuntime {
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] public static extern bool SetDllDirectory(string path);
 [DllImport("$dllLiteral", CallingConvention=CallingConvention.Cdecl, CharSet=CharSet.Unicode)] public static extern void Py_SetPath(string path);
 [DllImport("$dllLiteral", CallingConvention=CallingConvention.Cdecl)] public static extern void Py_Initialize();
 [DllImport("$dllLiteral", CallingConvention=CallingConvention.Cdecl)] public static extern int PyRun_SimpleString(byte[] code);
}
"@
[STEPythonRuntime]::SetDllDirectory($InstallDir) | Out-Null
[STEPythonRuntime]::Py_SetPath("$InstallDir\python35.zip;$InstallDir;$InstallDir\plugins;$taskRoot")
[STEPythonRuntime]::Py_Initialize()
$rootJson = ConvertTo-Json -InputObject $taskRoot -Compress
$installJson = ConvertTo-Json -InputObject $InstallDir -Compress
$scriptJson = ConvertTo-Json -InputObject $scriptPath -Compress
$logJson = ConvertTo-Json -InputObject $logPath -Compress
$bootstrap = @"
import os,sys,traceback
sys.stdout=open($logJson,'w',encoding='utf-8')
sys.stderr=sys.stdout
sys.frozen=True
sys.executable=os.path.join($installJson,'STESlicer.exe')
os.chdir($rootJson)
try:
 exec(compile(open($scriptJson,'rb').read(),$scriptJson,'exec'))
except BaseException:
 traceback.print_exc()
 print('HOST_SMOKE_FAILED')
sys.stdout.flush()
"@
$runResult = [STEPythonRuntime]::PyRun_SimpleString([System.Text.Encoding]::UTF8.GetBytes($bootstrap + [char]0))
Get-Content -LiteralPath $logPath -Encoding utf8
if ($runResult -ne 0 -or (Select-String -LiteralPath $logPath -Pattern 'HOST_SMOKE_FAILED' -Quiet)) { exit 1 }
