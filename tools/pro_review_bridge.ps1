[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Export", "Wait", "Import", "Status")]
    [string]$Mode,

    [Parameter(Mandatory = $true)]
    [string]$CheckpointDirectory,

    [ValidateRange(1, 3600)]
    [int]$PollSeconds = 30,

    [ValidateRange(0, 1000000)]
    [int]$MaxPolls = 0,

    [string]$DownloadsDirectory = (
        Join-Path ([Environment]::GetFolderPath("UserProfile")) "Downloads"
    ),

    [ValidateRange(0, 10000)]
    [int]$StabilityDelayMilliseconds = 250
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$script:Utf8Strict = New-Object System.Text.UTF8Encoding($false, $true)

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text
    )
    [IO.File]::WriteAllText($Path, $Text, $script:Utf8NoBom)
}

function Write-JsonAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value
    )
    $temporaryPath = "$Path.importing.$([Guid]::NewGuid().ToString('N'))"
    try {
        $json = $Value | ConvertTo-Json -Depth 8
        Write-Utf8NoBom -Path $temporaryPath -Text ($json + "`n")
        Move-Item -LiteralPath $temporaryPath -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-RequiredRegexValue {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][string[]]$Patterns,
        [Parameter(Mandatory = $true)][string]$FieldName
    )
    foreach ($pattern in $Patterns) {
        $match = [regex]::Match(
            $Text,
            $pattern,
            [Text.RegularExpressions.RegexOptions]::IgnoreCase -bor
                [Text.RegularExpressions.RegexOptions]::Multiline
        )
        if ($match.Success) {
            return $match.Groups[1].Value.Trim().Trim('"').Trim("'").Trim('`')
        }
    }
    throw "Missing required bridge identity field: $FieldName"
}

function Get-OptionalRegexValue {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][string[]]$Patterns
    )
    foreach ($pattern in $Patterns) {
        $match = [regex]::Match(
            $Text,
            $pattern,
            [Text.RegularExpressions.RegexOptions]::IgnoreCase -bor
                [Text.RegularExpressions.RegexOptions]::Multiline
        )
        if ($match.Success) {
            return $match.Groups[1].Value.Trim().Trim('"').Trim("'").Trim('`')
        }
    }
    return ""
}

function Assert-Sha256 {
    param(
        [Parameter(Mandatory = $true)][string]$Value,
        [Parameter(Mandatory = $true)][string]$FieldName
    )
    if ($Value -notmatch '^[0-9a-fA-F]{64}$') {
        throw "$FieldName must be a SHA-256 value"
    }
}

function Assert-GitObjectId {
    param(
        [Parameter(Mandatory = $true)][string]$Value,
        [Parameter(Mandatory = $true)][string]$FieldName
    )
    if ($Value -notmatch '^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$') {
        throw "$FieldName must be a 40- or 64-character Git object ID"
    }
}

function Get-BridgeIdentity {
    param([Parameter(Mandatory = $true)][string]$Directory)

    if (-not [IO.Path]::IsPathRooted($Directory)) {
        throw "CheckpointDirectory must be an absolute path"
    }
    $resolvedDirectory = [IO.Path]::GetFullPath($Directory)
    if (-not (Test-Path -LiteralPath $resolvedDirectory -PathType Container)) {
        throw "Checkpoint directory does not exist: $resolvedDirectory"
    }

    $checkpointId = Split-Path -Leaf $resolvedDirectory
    if ([string]::IsNullOrWhiteSpace($checkpointId)) {
        throw "Checkpoint directory must have a checkpoint ID leaf"
    }

    $requestPath = Join-Path $resolvedDirectory "REQUEST.md"
    $indexPath = Join-Path $resolvedDirectory "EVIDENCE_PACKAGE\INDEX.md"
    foreach ($requiredPath in @($requestPath, $indexPath)) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw "Required outbound file is unavailable: $requiredPath"
        }
    }

    $requestText = [IO.File]::ReadAllText($requestPath, $script:Utf8Strict)
    $checkpointSha = Get-RequiredRegexValue -Text $requestText -FieldName "checkpoint_sha" -Patterns @(
        '(?m)^\s*ending_checkpoint_sha\s*:\s*`?((?:[0-9a-f]{40}|[0-9a-f]{64}))`?\s*$',
        '(?m)^\s*(?:frozen_)?checkpoint_sha\s*:\s*`?((?:[0-9a-f]{40}|[0-9a-f]{64}))`?\s*$'
    )
    Assert-GitObjectId -Value $checkpointSha -FieldName "checkpoint_sha"

    $bridgeResultPath = Join-Path $resolvedDirectory "TASK4_RESULT.md"
    $evidenceBridgeSha = ""
    if (Test-Path -LiteralPath $bridgeResultPath -PathType Leaf) {
        $bridgeText = [IO.File]::ReadAllText($bridgeResultPath, $script:Utf8Strict)
        $evidenceBridgeSha = Get-OptionalRegexValue -Text $bridgeText -Patterns @(
            '(?m)^\s*bridge_task_sha\s*:\s*`?((?:[0-9a-f]{40}|[0-9a-f]{64}))`?\s*$',
            '(?m)^\s*evidence_bridge_sha\s*:\s*`?((?:[0-9a-f]{40}|[0-9a-f]{64}))`?\s*$'
        )
        if ($evidenceBridgeSha) {
            Assert-GitObjectId -Value $evidenceBridgeSha -FieldName "evidence_bridge_sha"
        }
    }

    $zipCandidates = @(
        Get-ChildItem -LiteralPath $resolvedDirectory -File -Filter "CERA_CHECKPOINT_*_EVIDENCE.zip"
    )
    if ($zipCandidates.Count -ne 1) {
        throw "Checkpoint must contain exactly one CERA checkpoint evidence ZIP"
    }
    $evidenceZipPath = $zipCandidates[0].FullName
    $zipHashPath = "$evidenceZipPath.sha256"
    if (-not (Test-Path -LiteralPath $zipHashPath -PathType Leaf)) {
        throw "Evidence ZIP hash file is unavailable: $zipHashPath"
    }
    $hashText = [IO.File]::ReadAllText($zipHashPath, $script:Utf8Strict)
    $evidenceZipSha256 = Get-RequiredRegexValue -Text $hashText -FieldName "evidence_zip_sha256" -Patterns @(
        '(?m)^\s*([0-9a-f]{64})(?:\s+.+)?\s*$'
    )
    Assert-Sha256 -Value $evidenceZipSha256 -FieldName "evidence_zip_sha256"
    $actualZipSha256 = Get-Sha256 -Path $evidenceZipPath
    if ($actualZipSha256 -ne $evidenceZipSha256.ToLowerInvariant()) {
        throw "Evidence ZIP hash mismatch"
    }

    $shortSha = $checkpointSha.Substring(0, 12).ToLowerInvariant()
    $expectedResponseFilename = "CERA_PRO_RESPONSE_${checkpointId}_${shortSha}.md"
    return [pscustomobject][ordered]@{
        checkpoint_id = $checkpointId
        checkpoint_sha = $checkpointSha.ToLowerInvariant()
        evidence_bridge_sha = $evidenceBridgeSha.ToLowerInvariant()
        evidence_zip_path = $evidenceZipPath
        evidence_zip_sha256 = $evidenceZipSha256.ToLowerInvariant()
        request_path = $requestPath
        evidence_index_path = $indexPath
        expected_downloaded_response_filename = $expectedResponseFilename
        expected_repository_response_path = (
            Join-Path $resolvedDirectory "PRO_RESPONSE_EVIDENCE_VERIFIED.md"
        )
        checkpoint_directory = $resolvedDirectory
    }
}

function Read-ExportReceipt {
    param([Parameter(Mandatory = $true)]$Identity)
    $receiptPath = Join-Path $Identity.checkpoint_directory "BRIDGE_EXPORT_RECEIPT.json"
    if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {
        throw "Bridge export receipt is unavailable; run Export first"
    }
    try {
        $receipt = [IO.File]::ReadAllText($receiptPath, $script:Utf8Strict) |
            ConvertFrom-Json
    }
    catch {
        throw "Bridge export receipt is unreadable: $($_.Exception.Message)"
    }
    if (
        $receipt.checkpoint_id -ne $Identity.checkpoint_id -or
        $receipt.checkpoint_sha -ne $Identity.checkpoint_sha -or
        $receipt.evidence_zip_sha256 -ne $Identity.evidence_zip_sha256 -or
        $receipt.expected_downloaded_response_filename -ne
            $Identity.expected_downloaded_response_filename -or
        $receipt.expected_repository_response_path -ne
            $Identity.expected_repository_response_path
    ) {
        throw "Bridge export receipt does not match the current checkpoint evidence"
    }
    return $receipt
}

function Get-ResponseIdentity {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)
    $content = $script:Utf8Strict.GetString($Bytes)
    if ($content.Length -gt 0 -and $content[0] -eq [char]0xFEFF) {
        $content = $content.Substring(1)
    }
    if ($content.Length -lt 200) {
        throw "Pro response is incomplete"
    }
    $identityWindow = $content.Substring(0, [Math]::Min(8192, $content.Length))
    $reviewedCheckpointId = Get-RequiredRegexValue -Text $identityWindow -FieldName "reviewed_checkpoint_id" -Patterns @(
        '(?m)^\s*reviewed_checkpoint_id\s*:\s*([^#\r\n]+?)\s*$'
    )
    $reviewedCheckpointSha = Get-RequiredRegexValue -Text $identityWindow -FieldName "reviewed_checkpoint_sha" -Patterns @(
        '(?m)^\s*reviewed_checkpoint_sha\s*:\s*([^#\r\n]+?)\s*$'
    )
    $reviewedZipSha = Get-RequiredRegexValue -Text $identityWindow -FieldName "reviewed_evidence_zip_sha256" -Patterns @(
        '(?m)^\s*reviewed_evidence_zip_sha256\s*:\s*([^#\r\n]+?)\s*$'
    )
    $reviewScope = Get-RequiredRegexValue -Text $identityWindow -FieldName "review_scope" -Patterns @(
        '(?m)^\s*review_scope\s*:\s*([^#\r\n]+?)\s*$'
    )
    Assert-GitObjectId -Value $reviewedCheckpointSha -FieldName "reviewed_checkpoint_sha"
    Assert-Sha256 -Value $reviewedZipSha -FieldName "reviewed_evidence_zip_sha256"
    if ($content -match '(?im)^\s*(?:review_disposition\s*:\s*)?(?:pending|placeholder|todo|tbd)\s*$') {
        throw "Pro response is a placeholder"
    }
    if ($content -notmatch '(?m)^\s*#{1,6}\s+\S+') {
        throw "Pro response is incomplete: review heading is missing"
    }
    return [pscustomobject][ordered]@{
        reviewed_checkpoint_id = $reviewedCheckpointId
        reviewed_checkpoint_sha = $reviewedCheckpointSha.ToLowerInvariant()
        reviewed_evidence_zip_sha256 = $reviewedZipSha.ToLowerInvariant()
        review_scope = $reviewScope.ToLowerInvariant()
    }
}

function Invoke-BridgeExport {
    param(
        [Parameter(Mandatory = $true)]$Identity,
        [Parameter(Mandatory = $true)][string]$DownloadsPath
    )
    if (-not [IO.Path]::IsPathRooted($DownloadsPath)) {
        throw "DownloadsDirectory must be an absolute path"
    }
    $resolvedDownloads = [IO.Path]::GetFullPath($DownloadsPath)
    if (-not (Test-Path -LiteralPath $resolvedDownloads -PathType Container)) {
        New-Item -ItemType Directory -Path $resolvedDownloads -Force | Out-Null
    }

    $shortSha = $Identity.checkpoint_sha.Substring(0, 12)
    $exportedZipPath = Join-Path $resolvedDownloads (
        "CERA_TO_PRO_$($Identity.checkpoint_id)_${shortSha}.zip"
    )
    $uploadMessagePath = Join-Path $resolvedDownloads (
        "CERA_TO_PRO_$($Identity.checkpoint_id)_${shortSha}_UPLOAD_MESSAGE.txt"
    )
    if (Test-Path -LiteralPath $exportedZipPath -PathType Leaf) {
        if ((Get-Sha256 -Path $exportedZipPath) -ne $Identity.evidence_zip_sha256) {
            throw "Existing Downloads export conflicts with the checkpoint evidence ZIP"
        }
    }
    else {
        Copy-Item -LiteralPath $Identity.evidence_zip_path -Destination $exportedZipPath
    }
    if ((Get-Sha256 -Path $exportedZipPath) -ne $Identity.evidence_zip_sha256) {
        throw "Exported evidence ZIP failed post-copy hash validation"
    }

    $message = @(
        "CERA checkpoint review package.",
        "",
        "Checkpoint ID: $($Identity.checkpoint_id)",
        "Frozen checkpoint SHA: $($Identity.checkpoint_sha)",
        "Evidence bridge SHA: $($Identity.evidence_bridge_sha)",
        "Evidence ZIP SHA-256: $($Identity.evidence_zip_sha256)",
        "Requested response filename: $($Identity.expected_downloaded_response_filename)",
        "",
        "Please inspect the attached evidence package and return the completed response as:",
        $Identity.expected_downloaded_response_filename,
        ""
    ) -join "`r`n"
    Write-Utf8NoBom -Path $uploadMessagePath -Text $message

    $receiptPath = Join-Path $Identity.checkpoint_directory "BRIDGE_EXPORT_RECEIPT.json"
    $receipt = [ordered]@{
        checkpoint_id = $Identity.checkpoint_id
        checkpoint_sha = $Identity.checkpoint_sha
        evidence_bridge_sha = $Identity.evidence_bridge_sha
        evidence_zip_sha256 = $Identity.evidence_zip_sha256
        exported_zip_path = $exportedZipPath
        upload_message_path = $uploadMessagePath
        expected_downloaded_response_filename = $Identity.expected_downloaded_response_filename
        expected_repository_response_path = $Identity.expected_repository_response_path
        exported_at_utc = [DateTime]::UtcNow.ToString("o", [Globalization.CultureInfo]::InvariantCulture)
    }
    Write-JsonAtomic -Path $receiptPath -Value $receipt
    Write-Output "CERA_REVIEW_PACKAGE_EXPORTED"
    Write-Output $exportedZipPath
    Write-Output $uploadMessagePath
}

function Invoke-BridgeImport {
    param(
        [Parameter(Mandatory = $true)]$Identity,
        [Parameter(Mandatory = $true)]$ExportReceipt,
        [Parameter(Mandatory = $true)][string]$DownloadsPath,
        [Parameter(Mandatory = $true)][int]$StabilityDelay
    )
    $responsePath = Join-Path (
        [IO.Path]::GetFullPath($DownloadsPath)
    ) $Identity.expected_downloaded_response_filename
    if (-not (Test-Path -LiteralPath $responsePath -PathType Leaf)) {
        throw "Expected Pro response is unavailable: $responsePath"
    }

    $firstItem = Get-Item -LiteralPath $responsePath
    $firstHash = Get-Sha256 -Path $responsePath
    if ($StabilityDelay -gt 0) {
        Start-Sleep -Milliseconds $StabilityDelay
    }
    $secondItem = Get-Item -LiteralPath $responsePath
    $secondHash = Get-Sha256 -Path $responsePath
    if (
        $firstItem.Length -ne $secondItem.Length -or
        $firstItem.LastWriteTimeUtc -ne $secondItem.LastWriteTimeUtc -or
        $firstHash -ne $secondHash
    ) {
        throw "Pro response is not stable and may be partially downloaded"
    }

    $responseBytes = [IO.File]::ReadAllBytes($responsePath)
    $responseIdentity = Get-ResponseIdentity -Bytes $responseBytes
    if (
        $responseIdentity.reviewed_checkpoint_id -ne $Identity.checkpoint_id -or
        $responseIdentity.reviewed_checkpoint_sha -ne $Identity.checkpoint_sha -or
        $responseIdentity.reviewed_evidence_zip_sha256 -ne $Identity.evidence_zip_sha256 -or
        $responseIdentity.review_scope -ne "evidence_verified"
    ) {
        throw "Pro response identity does not match the exported checkpoint evidence"
    }

    $destination = $Identity.expected_repository_response_path
    $responseSha256 = $secondHash
    if (Test-Path -LiteralPath $destination -PathType Leaf) {
        if ((Get-Sha256 -Path $destination) -ne $responseSha256) {
            throw "A conflicting verified Pro response already exists; creator resolution is required"
        }
    }
    else {
        $temporaryDestination = "$destination.importing.$([Guid]::NewGuid().ToString('N'))"
        try {
            [IO.File]::WriteAllBytes($temporaryDestination, $responseBytes)
            if ((Get-Sha256 -Path $temporaryDestination) -ne $responseSha256) {
                throw "Imported response failed byte-preservation validation"
            }
            Move-Item -LiteralPath $temporaryDestination -Destination $destination
        }
        finally {
            if (Test-Path -LiteralPath $temporaryDestination) {
                Remove-Item -LiteralPath $temporaryDestination -Force
            }
        }
    }

    $importReceiptPath = Join-Path $Identity.checkpoint_directory "BRIDGE_IMPORT_RECEIPT.json"
    $importReceipt = [ordered]@{
        checkpoint_id = $Identity.checkpoint_id
        checkpoint_sha = $Identity.checkpoint_sha
        evidence_zip_sha256 = $Identity.evidence_zip_sha256
        downloaded_response_path = $responsePath
        repository_response_path = $destination
        response_sha256 = $responseSha256
        identity_validation = "passed"
        imported_at_utc = [DateTime]::UtcNow.ToString("o", [Globalization.CultureInfo]::InvariantCulture)
    }
    Write-JsonAtomic -Path $importReceiptPath -Value $importReceipt
    Write-Output "CERA_PRO_RESPONSE_IMPORTED"
    Write-Output "CREATOR_AUTHORIZATION_REQUIRED"
}

function Invoke-BridgeWait {
    param(
        [Parameter(Mandatory = $true)]$Identity,
        [Parameter(Mandatory = $true)]$ExportReceipt,
        [Parameter(Mandatory = $true)][string]$DownloadsPath,
        [Parameter(Mandatory = $true)][int]$IntervalSeconds,
        [Parameter(Mandatory = $true)][int]$MaximumPolls,
        [Parameter(Mandatory = $true)][int]$StabilityDelay
    )
    $resolvedDownloads = [IO.Path]::GetFullPath($DownloadsPath)
    $responsePath = Join-Path $resolvedDownloads $Identity.expected_downloaded_response_filename
    $waitingPath = Join-Path $Identity.checkpoint_directory "WAITING_FOR_PRO_RESPONSE.md"
    $waitingSince = [DateTime]::UtcNow.ToString("o", [Globalization.CultureInfo]::InvariantCulture)
    if (Test-Path -LiteralPath $waitingPath -PathType Leaf) {
        $waitingText = [IO.File]::ReadAllText($waitingPath, $script:Utf8Strict)
        $existing = Get-OptionalRegexValue -Text $waitingText -Patterns @(
            '(?m)^\s*waiting_since_utc\s*:\s*(\S+)\s*$'
        )
        if ($existing) {
            $waitingSince = $existing
        }
    }
    $waiting = @(
        "status: waiting",
        "checkpoint_id: $($Identity.checkpoint_id)",
        "checkpoint_sha: $($Identity.checkpoint_sha)",
        "expected_downloaded_response: $responsePath",
        "expected_repository_destination: $($Identity.expected_repository_response_path)",
        "waiting_since_utc: $waitingSince",
        "poll_seconds: $IntervalSeconds",
        "provider_calls_while_waiting: 0",
        "database_or_story_writes_while_waiting: 0",
        "repository_source_changes_while_waiting: 0",
        ""
    ) -join "`n"
    Write-Utf8NoBom -Path $waitingPath -Text $waiting

    $pollCount = 0
    while ($true) {
        if (Test-Path -LiteralPath $responsePath -PathType Leaf) {
            Invoke-BridgeImport -Identity $Identity -ExportReceipt $ExportReceipt `
                -DownloadsPath $resolvedDownloads -StabilityDelay $StabilityDelay
            return
        }
        $pollCount += 1
        if ($MaximumPolls -gt 0 -and $pollCount -ge $MaximumPolls) {
            Write-Output "CERA_WAITING_FOR_PRO_RESPONSE"
            return
        }
        Start-Sleep -Seconds $IntervalSeconds
    }
}

function Invoke-BridgeStatus {
    param(
        [Parameter(Mandatory = $true)]$Identity,
        [Parameter(Mandatory = $true)][string]$DownloadsPath
    )
    $exportReceiptPath = Join-Path $Identity.checkpoint_directory "BRIDGE_EXPORT_RECEIPT.json"
    $importReceiptPath = Join-Path $Identity.checkpoint_directory "BRIDGE_IMPORT_RECEIPT.json"
    $waitingPath = Join-Path $Identity.checkpoint_directory "WAITING_FOR_PRO_RESPONSE.md"
    $responsePath = Join-Path (
        [IO.Path]::GetFullPath($DownloadsPath)
    ) $Identity.expected_downloaded_response_filename
    $exportStatus = "not_exported"
    if (Test-Path -LiteralPath $exportReceiptPath -PathType Leaf) {
        try {
            Read-ExportReceipt -Identity $Identity | Out-Null
            $exportStatus = "exported"
        }
        catch {
            $exportStatus = "invalid"
        }
    }
    $imported = $false
    $responseHash = ""
    if (
        (Test-Path -LiteralPath $Identity.expected_repository_response_path -PathType Leaf) -and
        (Test-Path -LiteralPath $importReceiptPath -PathType Leaf)
    ) {
        try {
            $importReceipt = [IO.File]::ReadAllText(
                $importReceiptPath, $script:Utf8Strict
            ) | ConvertFrom-Json
            $responseHash = Get-Sha256 -Path $Identity.expected_repository_response_path
            $imported = (
                $importReceipt.checkpoint_id -eq $Identity.checkpoint_id -and
                $importReceipt.checkpoint_sha -eq $Identity.checkpoint_sha -and
                $importReceipt.evidence_zip_sha256 -eq $Identity.evidence_zip_sha256 -and
                $importReceipt.repository_response_path -eq $Identity.expected_repository_response_path -and
                $importReceipt.response_sha256 -eq $responseHash -and
                $importReceipt.identity_validation -eq "passed"
            )
            if (-not $imported) {
                $responseHash = ""
            }
        }
        catch {
            $imported = $false
            $responseHash = ""
        }
    }
    $waitingStatus = if ($imported) {
        "not_waiting"
    }
    elseif (Test-Path -LiteralPath $waitingPath -PathType Leaf) {
        "waiting"
    }
    else {
        "not_waiting"
    }
    Write-Output "checkpoint ID: $($Identity.checkpoint_id)"
    Write-Output "checkpoint SHA: $($Identity.checkpoint_sha)"
    Write-Output "evidence ZIP hash: $($Identity.evidence_zip_sha256)"
    Write-Output "export status: $exportStatus"
    Write-Output "waiting status: $waitingStatus"
    Write-Output "response detected: $((Test-Path -LiteralPath $responsePath -PathType Leaf).ToString().ToLowerInvariant())"
    Write-Output "response imported: $($imported.ToString().ToLowerInvariant())"
    Write-Output "response hash: $responseHash"
    Write-Output "creator authorization status: required"
}

$bridgeIdentity = Get-BridgeIdentity -Directory $CheckpointDirectory
switch ($Mode) {
    "Export" {
        Invoke-BridgeExport -Identity $bridgeIdentity -DownloadsPath $DownloadsDirectory
    }
    "Wait" {
        $exportReceipt = Read-ExportReceipt -Identity $bridgeIdentity
        Invoke-BridgeWait -Identity $bridgeIdentity -ExportReceipt $exportReceipt `
            -DownloadsPath $DownloadsDirectory -IntervalSeconds $PollSeconds `
            -MaximumPolls $MaxPolls -StabilityDelay $StabilityDelayMilliseconds
    }
    "Import" {
        $exportReceipt = Read-ExportReceipt -Identity $bridgeIdentity
        Invoke-BridgeImport -Identity $bridgeIdentity -ExportReceipt $exportReceipt `
            -DownloadsPath $DownloadsDirectory -StabilityDelay $StabilityDelayMilliseconds
    }
    "Status" {
        Invoke-BridgeStatus -Identity $bridgeIdentity -DownloadsPath $DownloadsDirectory
    }
}
