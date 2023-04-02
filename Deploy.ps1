param(
    [string]$AppVersion,
    [string]$ConfigFile,
    [ValidateSet("Path1", "Path2", "Path3")]
    [string]$Destination,
    [string]$AppPoolName
)

# Cesta k síťovému disku
$NetworkSharePath = "\\NetworkShare\NoleApp"

# Předdefinované cesty na lokálním disku
$LocalPathUAT1 = "C:\NoleUAT\UAT1"
$LocalPathUAT2 = "C:\NoleUAT\UAT2"
$LocalPathUAT3 = "C:\NoleUAT\UAT3"

# Získání cílové cesty podle zvoleného předdefinovaného umístění
switch ($Destination) {
    "Path1" { 
        $LocalPath = $LocalPathUAT1
        $AppPoolName = "AppPoolUAT1"
    }
    "Path2" { 
        $LocalPath = $LocalPathUAT2
        $AppPoolName = "AppPoolUAT2"
    }
    "Path3" { 
        $LocalPath = $LocalPathUAT3
        $AppPoolName = "AppPoolUAT3"
    }
}

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