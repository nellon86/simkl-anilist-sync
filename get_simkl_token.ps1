# Obtiene el refresh token de Simkl en Windows, sin instalar nada.
# Uso: abre PowerShell, pega todo este archivo y pulsa Enter.
# El token se copia al portapapeles: pégalo en GitHub como Secret SIMKL_REFRESH_TOKEN.
# No lo guardes en ningún archivo ni lo subas al repo.

$clientId = "b62ee9436c568437c087d905e3a1c1f88c83e28fc9de9d35f5c207b5794ce2f9"
$ua = @{ "User-Agent" = "simkl-anilist-sync/1.0" }

$device = Invoke-RestMethod -Method Post -Uri "https://api.simkl.com/oauth2/device" -Headers $ua `
  -Body @{ client_id = $clientId; scope = "media:read" }
Write-Host "Autoriza la app en el navegador (código $($device.user_code))..."
Start-Process $device.verification_uri_complete

$interval = [int]$device.interval
$deadline = (Get-Date).AddSeconds([int]$device.expires_in)
$token = $null
while (-not $token -and (Get-Date) -lt $deadline) {
  Start-Sleep -Seconds $interval
  try {
    $res = Invoke-RestMethod -Method Post -Uri "https://api.simkl.com/oauth2/token" -Headers $ua -Body @{
      grant_type = "urn:ietf:params:oauth:grant-type:device_code"
      client_id = $clientId
      device_code = $device.device_code
    }
    $token = $res.refresh_token
  } catch {
    $err = ($_.ErrorDetails.Message | ConvertFrom-Json).error
    if ($err -eq "slow_down") { $interval += 5 }
    elseif ($err -ne "authorization_pending") { Write-Host "Error de Simkl: $err"; break }
  }
}

if ($token) {
  Set-Clipboard -Value $token
  Write-Host "Listo: refresh token copiado al portapapeles. Pegalo en GitHub como SIMKL_REFRESH_TOKEN."
} else {
  Write-Host "No se obtuvo el token. Vuelve a intentarlo."
}
