param(
  [Parameter(Mandatory=$true)]
  [string]$Path,
  [string]$ServerUrl = "http://localhost:8000",
  [string]$Source = "umap:adrasec78",
  [switch]$Apply
)

if(-not (Test-Path -LiteralPath $Path)){
  throw "Fichier introuvable : $Path"
}

$resolved=(Resolve-Path -LiteralPath $Path).Path
$applyValue=if($Apply){"true"}else{"false"}

$args=@(
  "-sS",
  "-X","POST",
  "$ServerUrl/api/v0.1/reference-sites/import/umap",
  "-F","source=$Source",
  "-F","apply=$applyValue",
  "-F","file=@$resolved"
)

$result=& curl.exe @args
if($LASTEXITCODE -ne 0){
  throw "Echec de l'appel HTTP vers SARCADE Server"
}

try{
  $json=$result | ConvertFrom-Json
  $json | ConvertTo-Json -Depth 8
}catch{
  $result
  throw "Réponse serveur invalide"
}

if(-not $Apply){
  Write-Host ""
  Write-Host "Aucun changement appliqué. Relancer avec -Apply après contrôle du résumé." -ForegroundColor Yellow
}
