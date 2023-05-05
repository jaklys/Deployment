param (
    [string]$exeFileName
)

$envVariable = [Environment]::GetEnvironmentVariable("DEPLOYDB")
$remotePath = "\\intranet\dfs-emea\GROUP\Ldn\Apps\NIX\Stage\Release\NoteOne"
$localPath = "C:\Barclays Capital\EFS\Nole"

if($envVariable -eq "True"){
    if(Test-Path "$remotePath\$exeFileName"){
        Copy-Item "$remotePath\$exeFileName" $localPath
        Start-Process "$localPath\$exeFileName"
    }
    else{
        Write-Host "File $exeFileName not found in $remotePath."
    }
}
else{
    Write-Host "DEPLOYDB environment variable is not set to True."
}



.\script.ps1 -exeFileName "nazev_souboru.exe"