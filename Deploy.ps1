param(
    [string]$AppVersion,
    [string]$ConfigFile
)

# Cesta k síťovému disku
$NetworkSharePath = "\\NetworkShare\NoleApp"

# Cílová cesta na lokálním disku
$LocalPath = "C:\NoleUAT"

# Import modulu pro práci s IIS
Import-Module WebAdministration

# Funkce pro zastavení a spuštění aplikace v IIS
function Restart-IISAppPool {
    param(
        [string]$AppPoolName
    )

    Write-Host "Restarting IIS Application Pool: $AppPoolName"
    Stop-WebAppPool $AppPoolName
    Start-WebAppPool $AppPoolName
}

# Zastavení aplikace v IIS
$AppPoolName = "NoleAppPool"
Restart-IISAppPool -AppPoolName $AppPoolName

# Smazání obsahu cílové složky
Write-Host "Deleting content of $LocalPath"
Remove-Item -Path "$LocalPath\*" -Recurse -Force

# Kopírování obsahu složky ze síťového disku do cílové složky
$SourcePath = Join-Path $NetworkSharePath $AppVersion
Write-Host "Copying files from $SourcePath to $LocalPath"
Copy-Item -Path "$SourcePath\*" -Destination $LocalPath -Recurse -Force

# Kopírování konfiguračního souboru do cílové složky
$ConfigFilePath = Join-Path $NetworkSharePath $ConfigFile
Write-Host "Copying config file $ConfigFilePath to $LocalPath"
Copy-Item -Path $ConfigFilePath -Destination $LocalPath -Force

# Spuštění aplikace v IIS
Restart-IISAppPool -AppPoolName $AppPoolName

Write-Host "Deployment completed"
