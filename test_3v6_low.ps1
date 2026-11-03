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

$v_3300 = 353.93
$off_3300 = Get-Offset-OptB $v_3300
$i_3300 = ($v_3300 - $off_3300) / $sens
$err_3300 = [Math]::Abs($i_3300 - 1.0876) / 1.0876 * 100

$v_3881 = 319.66
$off_3881 = Get-Offset-OptB $v_3881
$i_3881 = ($v_3881 - $off_3881) / $sens
$err_3881 = [Math]::Abs($i_3881 - 0.9252) / 0.9252 * 100

Write-Output ("3.6V / 3300: V_adc={0:F2}, Off={1:F2}, I={2:F4} mA, Err={3:F2}%" -f $v_3300, $off_3300, $i_3300, $err_3300)
Write-Output ("3.6V / 3881: V_adc={0:F2}, Off={1:F2}, I={2:F4} mA, Err={3:F2}%" -f $v_3881, $off_3881, $i_3881, $err_3881)
