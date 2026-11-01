param(
    [Parameter(Mandatory = $true)][string]$TextFile,
    [Parameter(Mandatory = $true)][string]$OutFile,
    [string]$Voice = "Microsoft Huihui Desktop",
    [int]$Rate = -1
)
Add-Type -AssemblyName System.Speech
$text = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try { $synth.SelectVoice($Voice) } catch { }
$synth.Rate = $Rate
$synth.Volume = 100
$synth.SetOutputToWaveFile($OutFile)
$synth.Speak($text)
$synth.Dispose()
