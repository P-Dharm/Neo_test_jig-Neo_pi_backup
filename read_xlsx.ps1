Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead('NEO_test.xlsx')

$ssEntry = $zip.GetEntry('xl/sharedStrings.xml')
$sharedStrings = @()
if ($ssEntry) {
    $stream = $ssEntry.Open()
    $reader = New-Object System.IO.StreamReader($stream)
    $xml = [xml]$reader.ReadToEnd()
    $reader.Close()
    foreach ($si in $xml.sst.si) {
        if ($si.t) {
            $sharedStrings += $si.t
        } elseif ($si.r) {
            $t = ($si.r | ForEach-Object { $_.t }) -join ''
            $sharedStrings += $t
        } else {
            $sharedStrings += ""
        }
    }
}

$sheetEntry = $zip.GetEntry('xl/worksheets/sheet1.xml')
$stream = $sheetEntry.Open()
$reader = New-Object System.IO.StreamReader($stream)
$sheetXml = [xml]$reader.ReadToEnd()
$reader.Close()
$zip.Dispose()

foreach ($row in $sheetXml.worksheet.sheetData.row) {
    $rowVals = @()
    foreach ($c in $row.c) {
        $val = $c.v
        if ($c.t -eq 's' -and $val -ne $null) {
            $val = $sharedStrings[[int]$val]
        }
        $rowVals += "$($c.r): $val"
    }
    Write-Output ($rowVals -join ' | ')
}
