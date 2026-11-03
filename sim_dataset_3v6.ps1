Add-Type -AssemblyName System.IO.Compression.FileSystem

$zipPath = "oct_27_10_27_Neo current test.xlsx"
$zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)

$ssEntry = $zip.GetEntry("xl/sharedStrings.xml")
$reader = New-Object System.IO.StreamReader($ssEntry.Open())
$ssXml = [xml]$reader.ReadToEnd()
$reader.Close()
$strings = @()
foreach ($si in $ssXml.sst.si) {
    if ($si.t) { $strings += $si.t }
    elseif ($si.r) { $strings += ($si.r | ForEach-Object { $_.t }) -join "" }
    else { $strings += "" }
}

$sEntry = $zip.GetEntry("xl/worksheets/sheet1.xml")
$reader = New-Object System.IO.StreamReader($sEntry.Open())
$sXml = [xml]$reader.ReadToEnd()
$reader.Close()
$zip.Dispose()

$rows = @{}
foreach ($row in $sXml.worksheet.sheetData.row) {
    $rIdx = [int]$row.r
    $rows[$rIdx] = @{}
    foreach ($c in $row.c) {
        $col = $c.r -replace '\d+',''
        $val = $c.v
        if ($c.t -eq "s") { $val = $strings[[int]$val] }
        $rows[$rIdx][$col] = $val
    }
}

function Get-Dynamic-Offset($v) {
    if ($v -le 340.0) { return 152.50 }
    if ($v -lt 355.0) {
        $t = ($v - 340.0) / (355.0 - 340.0)
        return 152.50 - $t * (152.50 - 117.78)
    }
    if ($v -le 450.0) { return 117.78 }
    if ($v -lt 650.0) {
        $t = ($v - 450.0) / (650.0 - 450.0)
        return 117.78 + $t * (152.50 - 117.78)
    }
    return 152.50
}

$sens_mid = 18.6428 * 10.0

Write-Output "=========================================================================================="
Write-Output "Evaluating All 3.6V Case 2 Rows in oct_27_10_27_Neo with Dynamic Offset"
Write-Output "=========================================================================================="
Write-Output ("{0,-4} | {1,-4} | {2,-6} | {3,-7} | {4,-8} | {5,-8} | {6,-7} | {7,-7} | {8,-6}" -f "Row", "Volt", "Res", "I_des", "OldMeas", "OldErr%", "DynOff", "NewMeas", "NewErr%")

foreach ($rIdx in 22..30) {
    $r = $rows[$rIdx]
    if (-not $r) { continue }
    $v_supply = [double]$r["B"]
    $res = [double]$r["C"]
    $i_des = [double]$r["D"]
    $old_meas = [double]$r["E"]
    $old_err = [double]$r["K"]

    $v_adc = $old_meas * $sens_mid + 152.50
    $new_off = Get-Dynamic-Offset $v_adc
    $new_meas = ($v_adc - $new_off) / $sens_mid
    $new_err = [Math]::Abs($new_meas - $i_des) / $i_des * 100.0
    Write-Output ("{0,4} | {1,3}V | {2,6} | {3,7:F4} | {4,8:F4} | {5,7:F2}% | {6,7:F2} | {7,7:F4} | {8,6:F2}%" -f `
        $rIdx, $v_supply, $res, $i_des, $old_meas, $old_err, $new_off, $new_meas, $new_err)
}
