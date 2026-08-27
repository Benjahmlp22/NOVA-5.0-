# OCR de Windows para NOVA.
#
# Windows trae un motor de OCR desde Windows 10, con los idiomas que
# tengas instalados (aquí, español). Es local, gratis y no hay nada que
# descargar. Sólo se llega por WinRT, igual que las voces buenas.
#
# Mismo patrón que onecore.ps1 y por el mismo motivo: arrancar
# powershell.exe cuesta 179 ms medidos. El proceso se queda vivo y cada
# petición cuesta lo que cuesta reconocer, no lo que cuesta arrancar.
#
# Protocolo, una línea por petición:
#   IDIOMAS            -> los que hay instalados, y OK
#   LEE <ruta.png>     -> UNA línea: "TEXTO <base64>" o "ERROR ..."
#
# La respuesta va en base64 por dos razones: el texto reconocido lleva
# saltos de línea (y el protocolo es de una línea por respuesta), y los
# acentos se corrompen según la página de códigos de la consola.

$ErrorActionPreference = 'Stop'

# UTF-8 en la salida. Sin esto, PowerShell escribe en la página de
# códigos de la consola (cp1252 aquí) y cualquier acento que salga sin
# pasar por base64 llega roto: "Español" se convirtió en "Espa?ol" la
# primera vez que se listaron los idiomas.
[Console]::OutputEncoding = [Text.Encoding]::UTF8

[Windows.Media.Ocr.OcrEngine, Windows.Media, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
Add-Type -AssemblyName System.Runtime.WindowsRuntime

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

$motor = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $motor) {
    Write-Output 'ERROR sin idiomas de OCR instalados'
    exit 1
}

Write-Output 'LISTO'

while ($true) {
    $linea = [Console]::In.ReadLine()
    if ($null -eq $linea) { break }
    $linea = $linea.Trim()
    if ($linea -eq '') { continue }
    if ($linea -eq 'SALIR') { break }

    try {
        if ($linea -eq 'IDIOMAS') {
            foreach ($l in [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages) {
                Write-Output "IDIOMA`t$($l.LanguageTag)`t$($l.DisplayName)"
            }
            Write-Output 'OK'
            continue
        }

        if ($linea.StartsWith('LEE ')) {
            $ruta = $linea.Substring(4).Trim()
            $archivo = Esperar ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ruta)) ([Windows.Storage.StorageFile])
            $flujo = Esperar ($archivo.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
            $decoder = Esperar ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($flujo)) ([Windows.Graphics.Imaging.BitmapDecoder])
            $bitmap = Esperar ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
            $resultado = Esperar ($motor.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

            # Línea a línea y no $resultado.Text: así se conservan los
            # saltos, que en una pantalla separan cosas distintas.
            $lineas = @()
            foreach ($l in $resultado.Lines) { $lineas += $l.Text }
            $texto = [string]::Join("`n", $lineas)

            $bitmap.Dispose()
            $flujo.Dispose()

            # UNA sola línea de respuesta. Mandar además un 'OK' dejaba
            # esa línea suelta en la cola, y la siguiente lectura
            # devolvía el texto de la imagen ANTERIOR.
            $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($texto))
            Write-Output "TEXTO $b64"
            continue
        }

        Write-Output "ERROR no entiendo $linea"
    }
    catch {
        Write-Output "ERROR $($_.Exception.Message -replace "`r?`n", ' ')"
    }
}
