[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Png,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string[]] $Keyword,

    [string] $LanguageTag = 'zh-Hans-CN'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Result([System.Collections.IDictionary] $Result, [int] $ExitCode) {
    $Result | ConvertTo-Json -Depth 8 -Compress
    exit $ExitCode
}

function Wait-WinRtOperation($Operation, [type] $ResultType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and
            $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
        } | Select-Object -First 1
    if (-not $method) { throw 'WinRT async bridge unavailable' }
    $closed = $method.MakeGenericMethod(@($ResultType))
    $task = $closed.Invoke($null, @($Operation))
    $task.GetAwaiter().GetResult()
}

function Get-KeywordWords([string] $Text) {
    @($Text.Split([char[]]@(' ', "`t", "`r", "`n"), [StringSplitOptions]::RemoveEmptyEntries))
}

function Add-ExactHitRect {
    param(
        [System.Collections.Generic.List[object]] $Hits,
        [string] $Term,
        [object[]] $SelectedWords
    )
    $rects = @($SelectedWords | ForEach-Object { $_.BoundingRect })
    $x1 = ($rects | Measure-Object -Property X -Minimum).Minimum
    $y1 = ($rects | Measure-Object -Property Y -Minimum).Minimum
    $x2 = ($rects | ForEach-Object { $_.X + $_.Width } | Measure-Object -Maximum).Maximum
    $y2 = ($rects | ForEach-Object { $_.Y + $_.Height } | Measure-Object -Maximum).Maximum
    $Hits.Add([ordered]@{
        keyword = $Term
        rect = [ordered]@{
            x = [math]::Round([double]$x1, 2)
            y = [math]::Round([double]$y1, 2)
            width = [math]::Round([double]($x2 - $x1), 2)
            height = [math]::Round([double]($y2 - $y1), 2)
        }
    })
}

$baseResult = [ordered]@{
    mode = 'Windows.Media.Ocr-local'
    status = 'error'
    language = $LanguageTag
    availableLanguages = [ordered]@{ 'zh-Hans' = $false; 'en' = $false }
    region = $null
    hits = @()
}
$debugPhase = 'start'

try {
    if ([IO.Path]::GetExtension($Png) -ine '.png' -or -not (Test-Path -LiteralPath $Png -PathType Leaf)) {
        $baseResult.status = 'invalid-input'
        Write-Result $baseResult 2
    }
    foreach ($term in $Keyword) {
        if ([string]::IsNullOrWhiteSpace($term)) {
            $baseResult.status = 'invalid-keyword'
            Write-Result $baseResult 2
        }
    }

    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $debugPhase = 'load-winrt-types'
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]

    $tags = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag })
    $debugPhase = 'languages'
    $baseResult.availableLanguages['zh-Hans'] = [bool]($tags | Where-Object { $_ -ieq 'zh-Hans' -or $_ -ilike 'zh-Hans-*' })
    $baseResult.availableLanguages['en'] = [bool]($tags | Where-Object { $_ -ieq 'en' -or $_ -ilike 'en-*' })

    $selectedTag = @($tags | Where-Object { $_ -ieq $LanguageTag -or $_ -ilike "$LanguageTag-*" } | Select-Object -First 1)
    if ($selectedTag.Count -eq 0) {
        $baseResult.status = 'language-unavailable'
        Write-Result $baseResult 0
    }
    $selectedTag = [string]$selectedTag[0]
    $baseResult.language = $selectedTag

    $fullPath = [IO.Path]::GetFullPath($Png)
    $storageFile = Wait-WinRtOperation -Operation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($fullPath)) `
        -ResultType ([Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime])
    $debugPhase = 'storage-file'
    $stream = Wait-WinRtOperation -Operation ($storageFile.OpenReadAsync()) `
        -ResultType ([Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType = WindowsRuntime])
    try {
        $decoder = Wait-WinRtOperation -Operation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) `
            -ResultType ([Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime])
        $debugPhase = 'decoder'
        $pixelWidth = [int]$decoder.PixelWidth
        $pixelHeight = [int]$decoder.PixelHeight
        $softwareBitmap = Wait-WinRtOperation -Operation ($decoder.GetSoftwareBitmapAsync()) `
            -ResultType ([Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime])
        $baseResult.region = [ordered]@{ left = 0; top = 0; width = $pixelWidth; height = $pixelHeight }

        try {
            $language = [Activator]::CreateInstance([Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime], @($selectedTag))
            $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
            $debugPhase = 'engine'
            if ($null -eq $engine) {
                $baseResult.status = 'language-unavailable'
                Write-Result $baseResult 0
            }
            $ocrResult = Wait-WinRtOperation -Operation ($engine.RecognizeAsync($softwareBitmap)) `
                -ResultType ([Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime])
            $debugPhase = 'recognized'
            $hits = [System.Collections.Generic.List[object]]::new()
            foreach ($line in $ocrResult.Lines) {
                $words = @($line.Words)
                foreach ($term in $Keyword) {
                    $hasWhitespace = $term.IndexOfAny([char[]]@(' ', "`t", "`r", "`n")) -ge 0
                    if ($hasWhitespace) {
                        $needle = @(Get-KeywordWords $term)
                        if ($needle.Count -eq 0 -or $needle.Count -gt $words.Count) { continue }
                        for ($i = 0; $i -le ($words.Count - $needle.Count); $i++) {
                            $isExact = $true
                            for ($j = 0; $j -lt $needle.Count; $j++) {
                                if (-not [string]::Equals([string]$words[$i + $j].Text, [string]$needle[$j], [StringComparison]::OrdinalIgnoreCase)) {
                                    $isExact = $false
                                    break
                                }
                            }
                            if ($isExact) {
                                $span = @($words[$i..($i + $needle.Count - 1)])
                                Add-ExactHitRect -Hits $hits -Term $term -SelectedWords $span
                            }
                        }
                        continue
                    }

                    # Chinese and other no-space literals may be split into adjacent OCR words.
                    # Join only contiguous chunks whose boxes have a small, bounded horizontal gap.
                    for ($i = 0; $i -lt $words.Count; $i++) {
                        $joined = ''
                        $last = [Math]::Min($words.Count - 1, $i + $term.Length - 1)
                        for ($j = $i; $j -le $last; $j++) {
                            if ($j -gt $i) {
                                $previousRect = $words[$j - 1].BoundingRect
                                $currentRect = $words[$j].BoundingRect
                                $gap = [double]$currentRect.X - ([double]$previousRect.X + [double]$previousRect.Width)
                                $maxGap = [Math]::Max(8.0, [Math]::Max([double]$previousRect.Height, [double]$currentRect.Height) * 0.4)
                                if ($gap -lt -2.0 -or $gap -gt $maxGap) { break }
                            }
                            $joined += [string]$words[$j].Text
                            if ([string]::Equals($joined, $term, [StringComparison]::OrdinalIgnoreCase)) {
                                $span = @($words[$i..$j])
                                Add-ExactHitRect -Hits $hits -Term $term -SelectedWords $span
                                break
                            }
                            if (-not $term.StartsWith($joined, [StringComparison]::OrdinalIgnoreCase)) { break }
                        }
                    }
                }
            }
            $baseResult.hits = @($hits.ToArray())
            $baseResult.status = if ($hits.Count -gt 0) { 'ok' } else { 'no-hit' }
            Write-Result $baseResult 0
        }
        finally {
            if ($softwareBitmap) { $softwareBitmap.Dispose() }
        }
    }
    finally {
        if ($stream) { $stream.Dispose() }
    }
}
catch {
    $baseResult.status = 'ocr-error'
    $baseResult.errorPhase = $debugPhase
    Write-Result $baseResult 2
}
