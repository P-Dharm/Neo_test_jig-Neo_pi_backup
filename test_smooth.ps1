$points = @(
    @{ Name="690 (5V)";   I_des=7.1429; V_adc=1500.95 },
    @{ Name="910 (5V)";   I_des=5.4348; V_adc=1185.22 },
    @{ Name="1000 (5V)";  I_des=4.9505; V_adc=1089.36 },
    @{ Name="3300 (5V)";  I_des=1.5106; V_adc= 396.68 },
    @{ Name="3881 (5V)";  I_des=1.2850; V_adc= 356.60 },
    @{ Name="5100 (5V)";  I_des=0.9785; V_adc= 335.26 },
    @{ Name="3300 (3.6V)"; I_des=1.0876; V_adc= 353.93 },
    @{ Name="3881 (3.6V)"; I_des=0.9252; V_adc= 319.66 }
)

function Calc-Current($v_adc) {
    $gain = 18.6428
    $rs = 10.0
    $sens = $gain * $rs # 186.428 mV/mA
    
    # Dynamic offset calibration for Case 2:
    # Near 1 mA (V_adc <= 340 mV), op-amp input CM foot shifts offset to 152.50 mV.
    # At normal Case 2 currents (V_adc >= 355 mV), offset returns to hardware VREF = 117.78 mV.
    # Between 340 mV and 355 mV: smooth linear transition.
    if ($v_adc -ge 355.0) {
        $offset = 117.78
    } elseif ($v_adc -le 340.0) {
        $offset = 152.50
    } else {
        $t = ($v_adc - 340.0) / (355.0 - 340.0)
        $offset = 152.50 - $t * (152.50 - 117.78)
    }
    
    $i = ($v_adc - $offset) / $sens
    return @{ Current = $i; Offset = $offset }
}

Write-Output "--- Testing Case 2 with Dynamic Offset (340 - 355 mV Transition) ---"
foreach ($p in $points) {
    $res = Calc-Current $p.V_adc
    $err = [Math]::Abs($res.Current - $p.I_des) / $p.I_des * 100.0
    "{0,-15} | Desired: {1,7:F4} mA | V_adc: {2,7:F2} mV | Offset: {3,6:F2} mV | Calc: {4,7:F4} mA | Error: {5,5:F2}%" -f $p.Name, $p.I_des, $p.V_adc, $res.Offset, $res.Current, $err
}
