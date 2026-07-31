$ErrorActionPreference = "Stop"
$Repo = "D:\AIChatBot\Cera"
$Prompt = ".chatgpt\codex-runs\2026-07-29T130000Z-hanezawa-v1-2-embodied-identity-speech-v1\PROMPT.md"
Set-Location $Repo
$Resolved = (Resolve-Path $Prompt).Path
Get-Content -Raw $Resolved | Set-Clipboard
Write-Host "Codex task prompt copied to clipboard: $Resolved"
Write-Host "Paste it into the active Codex session for D:\AIChatBot\Cera."
