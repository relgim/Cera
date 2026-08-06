[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $PythonExe,

    [Parameter(Mandatory = $true)]
    [string] $RunnerPath,

    [Parameter(Mandatory = $true)]
    [string] $WorkingDirectory,

    [Parameter(Mandatory = $true)]
    [string] $StdoutPath,

    [Parameter(Mandatory = $true)]
    [string] $StderrPath,

    [Parameter(Mandatory = $true)]
    [string] $ResultPath
)

$ErrorActionPreference = 'Stop'

$python = (Resolve-Path -LiteralPath $PythonExe).Path
$runner = (Resolve-Path -LiteralPath $RunnerPath).Path
$working = (Resolve-Path -LiteralPath $WorkingDirectory).Path
$stdout = [IO.Path]::GetFullPath($StdoutPath)
$stderr = [IO.Path]::GetFullPath($StderrPath)
$result = [IO.Path]::GetFullPath($ResultPath)

foreach ($path in @($stdout, $stderr, $result)) {
    if (Test-Path -LiteralPath $path) {
        throw "CERA terminal-capture output already exists: $path"
    }
    $parent = Split-Path -Parent $path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "CERA terminal-capture parent directory is absent: $parent"
    }
}

$process = Start-Process `
    -FilePath $python `
    -ArgumentList @("`"$runner`"") `
    -WorkingDirectory $working `
    -NoNewWindow `
    -Wait `
    -PassThru `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr

if (-not (Test-Path -LiteralPath $result -PathType Leaf)) {
    throw "CERA runner exited without publishing its terminal result: $result"
}

exit $process.ExitCode
