$points = @(
    @{ R="690 (5V)";   I_des=7.1429; V_adc=1500.95 },
    @{ R="910 (5V)";   I_des=5.4348; V_adc=1185.22 },
    @{ R="1000 (5V)";  I_des=4.9505; V_adc=1089.36 },
    @{ R="3300 (5V)";  I_des=1.5106; V_adc= 396.68 },
    @{ R="3881 (5V)";  I_des=1.2850; V_adc= 356.60 },
    @{ R="5100 (5V)";  I_des=0.9785; V_adc= 335.26 },
    @{ R="3300 (3.6V)"; I_des=1.0876; V_adc= 353.93 },
    @{ R="3881 (3.6V)"; I_des=0.9252; V_adc= 319.66 }
)

function Get-Case2Current($v_adc) {
    $gain = 18.6428
    $rs = 10.0
    $sens = $gain * $rs # 186.428 mV/mA
    
    # Transition function for offset:
    # Above 370 mV: hardware VREF offset = 117.78 mV
    # Below 335 mV: low-current offset = 152.50 mV
    # Between 335 and 370 mV: linear interpolation
    if ($v_adc -ge 370.0) {
        $offset = 117.78
    } elseif ($v_adc -le 335.0) {
        $offset = 152.50
    } else {
        $frac = ($v_adc - 335.0) / (370.0 - 335.0)
        $offset = 152.50 - $frac * (152.50 - 117.78)
    }
    
    $current = ($v_adc - $offset) / $sens
    return @{ Current = $current; Offset = $offset }
}

Write-Output "--- Testing Knee / Offset Transition on Case 2 ---"
foreach ($p in $points) {
    $res = Get-Case2Current $p.V_adc
    $i_calc = $res.Current
    $err = [Math]::Abs($i_calc - $p.I_des) / $p.I_des * 100.0
    "{0,-15} | Desired: {1,7:F4} mA | V_adc: {2,7:F2} mV | Offset: {3,6:F2} mV | Calc: {4,7:F4} mA | Error: {5,5:F2}%" -f $p.R, $p.I_des, $p.V_adc, $res.Offset, $i_calc, $err
}
