$case2_data = @(
    # 5.0V Points
    @{ V=5.0; R="690";  I_des=7.1429; V_adc=1500.95 },
    @{ V=5.0; R="910";  I_des=5.4348; V_adc=1185.22 },
    @{ V=5.0; R="1000"; I_des=4.9505; V_adc=1089.36 },
    @{ V=5.0; R="3300"; I_des=1.5106; V_adc= 396.68 },
    @{ V=5.0; R="3881"; I_des=1.2850; V_adc= 356.60 },
    @{ V=5.0; R="5100"; I_des=0.9785; V_adc= 335.26 },

    # 3.6V Points
    @{ V=3.6; R="470";  I_des=7.5000; V_adc=1589.60 },
    @{ V=3.6; R="503";  I_des=7.0175; V_adc=1455.90 },
    @{ V=3.6; R="570";  I_des=6.2069; V_adc=1331.70 },
    @{ V=3.6; R="590";  I_des=6.0000; V_adc=1291.80 },
    @{ V=3.6; R="690";  I_des=5.1429; V_adc=1122.50 },
    @{ V=3.6; R="910";  I_des=3.9130; V_adc= 876.80 },
    @{ V=3.6; R="1000"; I_des=3.5644; V_adc= 811.20 }
)

$gain = 18.6428
$rs = 10.0
$sens = $gain * $rs

function Get-Offset-OptA($v) {
    # Option A: Simple transition around 340-355 mV, 117.78 mV everywhere above
    if ($v -ge 355.0) { return 117.78 }
    if ($v -le 340.0) { return 152.50 }
    $t = ($v - 340.0) / (355.0 - 340.0)
    return 152.50 - $t * (152.50 - 117.78)
}

function Get-Offset-OptB($v) {
    # Option B: Notch at 355-450 mV (where 3803/3300 sit)
    # <= 340 mV: 152.50 mV (5100 ohm)
    # 355 - 450 mV: 117.78 mV (3803 & 3300 ohm)
    # >= 700 mV: 152.50 mV (higher currents where gain slope is higher)
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

Write-Output "--- Comparison of Option A vs Option B ---"
foreach ($p in $case2_data) {
    $v = $p.V_adc
    
    $offA = Get-Offset-OptA $v
    $iA = ($v - $offA) / $sens
    $errA = [Math]::Abs($iA - $p.I_des) / $p.I_des * 100.0

    $offB = Get-Offset-OptB $v
    $iB = ($v - $offB) / $sens
    $errB = [Math]::Abs($iB - $p.I_des) / $p.I_des * 100.0

    "{0,4}V | {1,5} ohm | Des: {2,7:F4} mA | V_adc: {3,7:F2} | OptA: {4,7:F4} ({5,4:F1}%) | OptB: {6,7:F4} ({7,4:F1}%)" -f `
        $p.V, $p.R, $p.I_des, $v, $iA, $errA, $iB, $errB
}
