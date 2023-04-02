param(
    [string]$AppVersion,
    [string]$ConfigFile,
    [ValidateSet("UAT1", "UAT2", "UAT3")]
    [string]$Destination,
    [string]$AppPoolName
)

# Cesta k síťovému disku
$NetworkSharePath = "\\NetworkShare\NoleApp"

# Předdefinované cesty na lokálním disku
$LocalPathUAT1 = "C:\UAT1"
$LocalPathUAT2 = "C:\UAT2"
$LocalPathUAT3 = "C:\UAT3"

# Získání cílové cesty podle zvoleného předdefinovaného umístění
switch ($Destination) {
    "UAT1" { 
        $LocalPath = $LocalPathUAT1
        $AppPoolName = "AppPoolUAT1"
    }
    "UAT2" { 
        $LocalPath = $LocalPathUAT2
        $AppPoolName = "AppPoolUAT2"
    }
    "UAT3" { 
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


.\Deploy-NoleApp.ps1 -AppVersion "1.0.0" -Destination "UAT1" -AppPoolName "AppPoolUAT1"

curl -X POST "https://<ra_server>/datamanagement/a/api/<api_version>/executions" -H "accept: application/json" -H "Content-Type: application/json" -H "Authorization: Bearer <api_token>" -d "{ \"applicationProcessId\": \"<process_id>\", \"environmentId\": \"<environment_id>\", \"applicationProcessProperties\": [ { \"name\": \"AppVersion\", \"value\": \"%AppVersion%\" }, { \"name\": \"ConfigFile\", \"value\": \"%ConfigFile%\" }, { \"name\": \"Destination\", \"value\": \"%Destination%\" }, { \"name\": \"AppPoolName\", \"value\": \"%AppPoolName%\" } ] }"
