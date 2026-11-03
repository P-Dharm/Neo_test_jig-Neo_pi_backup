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

$slope = 195.4021
$intercept = 107.8944

Write-Output "--- Single Linear Line (Gain=19.54, Offset=107.89) ---"
foreach ($p in $case2_data) {
    $v = $p.V_adc
    $i = ($v - $intercept) / $slope
    $err = [Math]::Abs($i - $p.I_des) / $p.I_des * 100.0
    "{0,4}V | {1,5} ohm | Des: {2,7:F4} mA | V_adc: {3,7:F2} | Calc: {4,7:F4} | Err: {5,4:F1}%" -f `
        $p.V, $p.R, $p.I_des, $v, $i, $err
}
