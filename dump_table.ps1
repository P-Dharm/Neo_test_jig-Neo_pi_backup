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

$rows = @()
foreach ($row in $sheetXml.worksheet.sheetData.row) {
    $dict = @{}
    foreach ($c in $row.c) {
        $val = $c.v
        if ($c.t -eq 's' -and $val -ne $null) {
            $val = $sharedStrings[[int]$val]
        }
        $col = [regex]::Match($c.r, '^[A-Z]+').Value
        $dict[$col] = $val
    }
    if ($dict['B'] -and ($dict['B'] -eq '5' -or $dict['B'] -eq '3.6')) {
        $rows += [PSCustomObject]@{
            Row     = $row.r
            Voltage = $dict['B']
            R_ohms  = $dict['C']
            Desired = [double]$dict['D']
            Board1  = [double]$dict['E']
            Board2  = [double]$dict['F']
            Board3  = [double]$dict['G']
            Err1    = $dict['K']
            Err2    = $dict['L']
            Err3    = $dict['M']
        }
    }
}

$rows | Format-Table -AutoSize
