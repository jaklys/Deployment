param (
    [string]$exeFileName,
    [string]$env
)

$envVariable = [Environment]::GetEnvironmentVariable("DEPLOYDB")
$remotePath = "\\intranet\dfs-emea\GROUP\Ldn\Apps\NIX\Stage\Release\NoteOne"
$localPath = "C:\Barclays Capital\EFS\Nole"

if($envVariable -eq "True"){
    if(Test-Path "$remotePath\$exeFileName"){
        Copy-Item "$remotePath\$exeFileName" $localPath

        # Upravená část pro spuštění s argumenty
        $serverPath = "Server=GBRDSM050002060\"
        switch($env){
            'UAT1' {
                $arguments = '--connection "' + $serverPath + 'NEW_SQLVIRT_UAT;Database=NoteOne_UAT1;IntegratedSecurity=true;Trusted_Connection=True;TrustServerCertificate=True;MultipleActiveResultSets=true"'
            }
            'UAT2' {
                # upravte následující řádek podle potřeby
            }
            'UAT3' {
                # upravte následující řádek podle potřeby
            }
            'DEV1' {
                $arguments = '--connection "' + $serverPath + 'NEW_SQLVIRT_DEV;Database=NoteOne_DEV1;IntegratedSecurity=true;Trusted_Connection=True;TrustServerCertificate=True;MultipleActiveResultSets=true"'
            }
            'DEV2' {
                $arguments = '--connection "' + $serverPath + 'NEW_SQLVIRT_DEV;Database=NoteOne_DEV2;IntegratedSecurity=true;Trusted_Connection=True;TrustServerCertificate=True;MultipleActiveResultSets=true"'
            }
            'DEV3' {
                $arguments = '--connection "' + $serverPath + 'NEW_SQLVIRT_DEV;Database=NoteOne_DEV3;IntegratedSecurity=true;Trusted_Connection=True;TrustServerCertificate=True;MultipleActiveResultSets=true"'
            }
            'PROD' {
                # upravte následující řádek podle potřeby
            }
            'DR' {
                # upravte následující řádek podle potřeby
            }
        }
        Start-Process "$localPath\$exeFileName" -ArgumentList $arguments
    }
    else{
        Write-Host "File $exeFileName not found in $remotePath."
    }
}
else{
    Write-Host "DEPLOYDB environment variable is not set to True."
}
