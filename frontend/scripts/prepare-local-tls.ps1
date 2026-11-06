param([string]$MkcertPath = 'mkcert')
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$tlsDir = Join-Path $repoRoot 'local/tls'
New-Item -ItemType Directory -Force -Path $tlsDir | Out-Null
$previousCaRoot = $env:CAROOT
try {
    $env:CAROOT = Join-Path $tlsDir 'ca'
    # Generate only. Trust installation is a separate action performed by the user.
    if ((Test-Path (Join-Path $tlsDir 'localhost.pem')) -or (Test-Path (Join-Path $tlsDir 'localhost-key.pem'))) {
        throw 'TLS material already exists. Review it before replacing; no files were overwritten.'
    }
    & $MkcertPath -cert-file (Join-Path $tlsDir 'localhost.pem') -key-file (Join-Path $tlsDir 'localhost-key.pem') localhost
    if ($LASTEXITCODE -ne 0) { throw 'Certificate generation failed.' }
    $rootCert = [Security.Cryptography.X509Certificates.X509Certificate2]::new((Join-Path $env:CAROOT 'rootCA.pem'))
    Write-Host "Generated localhost certificate. CA SHA256: $($rootCert.GetCertHashString([Security.Cryptography.HashAlgorithmName]::SHA256))"
    Write-Host 'No trust store was changed. See README for the user-operated trust step.'
} finally {
    if ($null -eq $previousCaRoot) { Remove-Item Env:CAROOT -ErrorAction SilentlyContinue }
    else { $env:CAROOT = $previousCaRoot }
}
