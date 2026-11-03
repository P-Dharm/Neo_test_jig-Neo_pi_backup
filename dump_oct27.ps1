Add-Type -AssemblyName System.IO.Compression.FileSystem
$xlsxPath = "oct_27_10_27_Neo current test.xlsx"
$zip = [System.IO.Compression.ZipFile]::OpenRead($xlsxPath)

Write-Output "=== Entries in ZIP ==="
foreach ($entry in $zip.Entries) {
    if ($entry.FullName -like "*sheet*.xml" -or $entry.FullName -like "*sharedStrings.xml" -or $entry.FullName -like "*workbook.xml") {
        Write-Output $entry.FullName
    }
}

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

Write-Output "`n=== Sheet 1 Data ==="
$sheetEntry = $zip.GetEntry('xl/worksheets/sheet1.xml')
if ($sheetEntry) {
    $stream = $sheetEntry.Open()
    $reader = New-Object System.IO.StreamReader($stream)
    $sheetXml = [xml]$reader.ReadToEnd()
    $reader.Close()

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
}

$zip.Dispose()
