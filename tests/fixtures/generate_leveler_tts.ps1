param([string]$Destination)
$ErrorActionPreference = 'Stop'
$text = 'This is a synthetic test recording for the Fauxphonic audio application. A quiet phrase and a louder phrase should become easier to follow. The pauses must stay in place, and the original recording must remain unchanged.'
$voice = New-Object -ComObject SAPI.SpVoice
$stream = New-Object -ComObject SAPI.SpFileStream
$stream.Open((Join-Path $Destination 'speech.wav'), 3, $false)
$voice.AudioOutputStream = $stream
$voice.Rate = 0
$voice.Speak($text) | Out-Null
$stream.Close()
@{ text = $text; voice = $voice.Voice.GetDescription(); generator = 'Windows SAPI.SpVoice via Windows PowerShell 5.1 STA'; created = (Get-Date -Format o); source = 'New app-generated synthetic test speech; no user recording copied.' } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Destination 'provenance.json') -Encoding utf8
