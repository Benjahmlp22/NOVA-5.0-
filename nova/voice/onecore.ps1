# Sintetizador de voz OneCore para NOVA.
#
# Windows tiene DOS juegos de voces y no son el mismo. SAPI5 (lo que ve
# pyttsx3) sólo enseña las "Desktop", que en español son dos y las dos
# de mujer. OneCore tiene cinco, incluidas Pablo y Raul, que son de
# hombre, y suenan menos metálicas.
#
# A OneCore sólo se llega por WinRT, y a WinRT desde Python haría falta
# instalar algo. Desde PowerShell no: ya está en el sistema.
#
# Este script se queda VIVO leyendo líneas de stdin. Es lo único que
# hace viable la idea: arrancar powershell.exe cuesta 179 ms medidos, y
# NOVA sintetiza frase a frase mientras el modelo escribe. Pagando el
# arranque una vez, cada frase cuesta 11 ms.
#
# Protocolo, una línea por petición:
#   VOCES                     -> lista de voces, una por línea, y OK
#   VOZ <nombre>              -> elige voz, responde OK o ERROR
#   VELOCIDAD <n>             -> 1.0 es normal; responde OK
#   DI <base64> <ruta.wav>    -> sintetiza y responde OK o ERROR
#
# El texto va en base64 a propósito: por stdin, los acentos se
# corrompían según la página de códigos de la consola, y "cañón" llegaba
# como otra cosa. Con base64 no hay nada que interpretar.

$ErrorActionPreference = 'Stop'

[Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media, ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType=WindowsRuntime] | Out-Null
Add-Type -AssemblyName System.Runtime.WindowsRuntime

# WinRT devuelve tareas asíncronas y PowerShell 5.1 no sabe esperarlas
# solo. Este es el puente estándar: sacar AsTask por reflexión.
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {
        $_.Name -eq 'AsTask' -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1

function Esperar($operacion, $tipo) {
    $tarea = $asTask.MakeGenericMethod($tipo).Invoke($null, @($operacion))
    $tarea.Wait()
    $tarea.Result
}

$synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer

Write-Output 'LISTO'

while ($true) {
    $linea = [Console]::In.ReadLine()
    if ($null -eq $linea) { break }
    $linea = $linea.Trim()
    if ($linea -eq '') { continue }
    if ($linea -eq 'SALIR') { break }

    try {
        if ($linea -eq 'VOCES') {
            foreach ($v in [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices) {
                Write-Output "VOZ`t$($v.DisplayName)`t$($v.Language)`t$($v.Gender)"
            }
            Write-Output 'OK'
            continue
        }

        if ($linea.StartsWith('VOZ ')) {
            $quiero = $linea.Substring(4).Trim()
            $v = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices |
                 Where-Object { $_.DisplayName -eq $quiero } | Select-Object -First 1
            if ($null -eq $v) { Write-Output "ERROR no existe $quiero"; continue }
            $synth.Voice = $v
            Write-Output 'OK'
            continue
        }

        if ($linea.StartsWith('VELOCIDAD ')) {
            $v = [double]$linea.Substring(10).Trim()
            # WinRT acepta de 0.5 a 6.0; por encima de 2 no se entiende
            # nada, así que se corta antes de que lo haga Windows.
            $synth.Options.SpeakingRate = [Math]::Max(0.5, [Math]::Min(2.0, $v))
            Write-Output 'OK'
            continue
        }

        if ($linea.StartsWith('DI ')) {
            $partes = $linea.Substring(3).Split(' ', 2)
            $texto = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($partes[0]))
            $destino = $partes[1]

            $stream = Esperar $synth.SynthesizeTextToStreamAsync($texto) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
            $reader = New-Object Windows.Storage.Streams.DataReader($stream)
            Esperar $reader.LoadAsync($stream.Size) ([UInt32]) | Out-Null
            $bytes = New-Object byte[] $stream.Size
            $reader.ReadBytes($bytes)
            [IO.File]::WriteAllBytes($destino, $bytes)
            $reader.Dispose()
            $stream.Dispose()
            Write-Output 'OK'
            continue
        }

        Write-Output "ERROR no entiendo $linea"
    }
    catch {
        # Nunca morir por una frase: NOVA se quedaría muda a mitad de
        # conversación y sin saber por qué.
        Write-Output "ERROR $($_.Exception.Message -replace "`r?`n", ' ')"
    }
}
