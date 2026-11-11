# SARCADE - Installation des prerequis Windows
# Executer dans PowerShell en tant qu'administrateur.
# Windows 10/11 x64 recommande. Redemarrer si WSL/virtualisation le demande.

$ErrorActionPreference = "Stop"

function Info($m) { Write-Host "[SARCADE] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[ATTENTION] $m" -ForegroundColor Yellow }

function Require-Admin {
    $id=[Security.Principal.WindowsIdentity]::GetCurrent()
    $p=New-Object Security.Principal.WindowsPrincipal($id)
    if(-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
        throw "Relancez PowerShell en tant qu'administrateur."
    }
}

function Has-Command($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

Require-Admin
Info "Verification de winget..."
if(-not (Has-Command "winget")){
    throw "winget est absent. Installez/mettez a jour App Installer depuis Microsoft Store, puis relancez ce script."
}

Info "Activation des composants Windows utiles a WSL 2..."
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart | Out-Null
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart | Out-Null

Info "Installation / mise a jour de WSL..."
try { wsl.exe --install --no-distribution | Out-Host } catch { Warn "WSL demande peut-etre un redemarrage avant finalisation." }
try { wsl.exe --set-default-version 2 | Out-Host } catch { Warn "Impossible de forcer WSL2 maintenant. Un redemarrage peut etre necessaire." }

Info "Installation de Git..."
winget install --id Git.Git -e --source winget --accept-source-agreements --accept-package-agreements

Info "Installation de Docker Desktop..."
winget install --id Docker.DockerDesktop -e --source winget --accept-source-agreements --accept-package-agreements

# Recharge PATH courant pour Git si possible.
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")

Write-Host ""
Info "Verification..."
if(Has-Command "git"){ git --version; Ok "Git installe" } else { Warn "Git sera disponible apres ouverture d'un nouveau terminal." }

$dockerExe="$Env:ProgramFiles\Docker\Docker\resources\bin\docker.exe"
if(Test-Path $dockerExe){
    & $dockerExe --version
    & $dockerExe compose version
    Ok "Docker CLI et Docker Compose v2 installes"
} else {
    Warn "Docker Desktop est installe mais ses commandes peuvent necessiter un nouveau terminal/redemarrage."
}

Write-Host ""
Info "Installation terminee."
Write-Host "1. Redemarrez Windows si WSL ou Windows le demande."
Write-Host "2. Lancez Docker Desktop et attendez l'etat Engine running."
Write-Host "3. Ouvrez un nouveau PowerShell."
Write-Host "4. Lancez: docker version"
Write-Host "5. Lancez: docker compose version"
Write-Host "6. Clonez SARCADE: git clone https://github.com/sdubos-consulting/sarcade-server.git"
Write-Host "7. Puis: cd sarcade-server\demo"
Write-Host "8. Puis: .\start-demo.ps1"
