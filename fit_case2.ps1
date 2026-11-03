$points = @(
    @{ True = 8.62; Meas = 8.87 },
    @{ True = 8.33; Meas = 8.69 },
    @{ True = 7.14; Meas = 7.49 },
    @{ True = 5.43; Meas = 5.71 },
    @{ True = 4.90; Meas = 5.11 },
    @{ True = 1.50; Meas = 1.49 },
    @{ True = 1.29; Meas = 1.27 }
)

$gain = 18.6428
$rs = 10.0
$offset = 117.78

$n = $points.Count
$sum_x = 0.0
$sum_y = 0.0
$sum_xx = 0.0
$sum_xy = 0.0

foreach ($p in $points) {
    $x = $p.True
    $y = $p.Meas * $gain * $rs + $offset
    $sum_x += $x
    $sum_y += $y
    $sum_xx += $x * $x
    $sum_xy += $x * $y
}

$slope = ($n * $sum_xy - $sum_x * $sum_y) / ($n * $sum_xx - $sum_x * $sum_x)
$intercept = ($sum_y - $slope * $sum_x) / $n

Write-Output ("Slope (Sensitivity): {0:N4} mV/mA" -f $slope)
Write-Output ("Intercept (Offset): {0:N4} mV" -f $intercept)
Write-Output ("Fitted Gain (Slope / 10): {0:N4}" -f ($slope / 10.0))
