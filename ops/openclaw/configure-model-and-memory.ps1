param(
  [string]$Model = "",
  [switch]$EnableLocalMemory,
  [string]$EmbeddingModelPath = "",
  [switch]$RestartGateway
)

$ErrorActionPreference = "Stop"

if (-not $Model) {
  $Model = [Environment]::GetEnvironmentVariable("OPENCLAW_SENTINEL_MODEL")
}
if (-not $Model) {
  try {
    $Model = (& openclaw config get agents.defaults.model.primary 2>$null).Trim()
  } catch {
    $Model = ""
  }
}

if (-not $EmbeddingModelPath) {
  $EmbeddingModelPath = [Environment]::GetEnvironmentVariable("OPENCLAW_SENTINEL_LOCAL_EMBEDDING_MODEL")
}
if (-not $EmbeddingModelPath) {
  $EmbeddingModelPath = "hf:ggml-org/embeddinggemma-300m-qat-q8_0-GGUF/embeddinggemma-300m-qat-Q8_0.gguf"
}

if ($Model) {
  & openclaw models set $Model
}

if ($EnableLocalMemory) {
  & openclaw config set agents.defaults.memorySearch.enabled true
  & openclaw config set agents.defaults.memorySearch.provider local
  & openclaw config set agents.defaults.memorySearch.local.modelPath $EmbeddingModelPath
} else {
  & openclaw config set agents.defaults.memorySearch.enabled false
  try {
    & openclaw config unset agents.defaults.memorySearch.provider *> $null
  } catch {
  }
  try {
    & openclaw config unset agents.defaults.memorySearch.local.modelPath *> $null
  } catch {
  }
}

& openclaw config validate

if ($RestartGateway) {
  & openclaw gateway restart
}

Write-Host ""
if ($Model) {
  Write-Host "Primary model configured: $Model"
} else {
  Write-Host "Primary model unchanged. Set OPENCLAW_SENTINEL_MODEL or pass -Model to configure it explicitly."
}
if ($EnableLocalMemory) {
  Write-Host "Local memory enabled with model: $EmbeddingModelPath"
  Write-Host "Run 'openclaw memory status --deep' to confirm node-llama-cpp is available."
} else {
  Write-Host "Memory search disabled."
}
