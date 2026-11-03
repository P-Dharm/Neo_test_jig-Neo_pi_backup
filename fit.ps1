$delta_V = 334.898 - 258.796
$delta_I = 0.9785 - 0.7045
$slope = $delta_V / $delta_I
$gain = $slope / 10.0
$offset = 334.898 - $slope * 0.9785
Write-Output ("Slope: {0:F3} mV/mA" -f $slope)
Write-Output ("Gain: {0:F3}" -f $gain)
Write-Output ("Offset: {0:F3} mV" -f $offset)
