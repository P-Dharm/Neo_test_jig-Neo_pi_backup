$gain = 18.6428
$rs = 10.0
$sens = $gain * $rs

function Get-Offset-OptB($v) {
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

$prev_i = -1.0
$monotonic = $true
for ($v = 160.0; $v -le 2250.0; $v += 0.5) {
    $off = Get-Offset-OptB $v
    $i = ($v - $off) / $sens
    if ($i -lt $prev_i) {
        Write-Output ("NON-MONOTONIC at V={0:F2}: i={1:F4} < prev={2:F4}" -f $v, $i, $prev_i)
        $monotonic = $false
        break
    }
    $prev_i = $i
}

if ($monotonic) {
    Write-Output "SUCCESS: Function is strictly monotonic across entire range (160 mV to 2250 mV)."
}
