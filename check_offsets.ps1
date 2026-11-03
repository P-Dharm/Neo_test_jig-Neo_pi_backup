$points_3v6 = @(
    @{ R="470";  I_des=7.5000; V_adc=1589.6 },
    @{ R="503";  I_des=7.0175; V_adc=1455.9 },
    @{ R="570";  I_des=6.2069; V_adc=1331.7 },
    @{ R="590";  I_des=6.0000; V_adc=1291.8 },
    @{ R="690";  I_des=5.1429; V_adc=1122.5 },
    @{ R="910";  I_des=3.9130; V_adc= 876.8 },
    @{ R="1000"; I_des=3.5644; V_adc= 811.2 },
    @{ R="3300"; I_des=1.0876; V_adc= 353.93 },
    @{ R="3881"; I_des=0.9252; V_adc= 319.66 }
)

$points_5v = @(
    @{ R="690";  I_des=7.1429; V_adc=1500.95 },
    @{ R="910";  I_des=5.4348; V_adc=1185.22 },
    @{ R="1000"; I_des=4.9505; V_adc=1089.36 },
    @{ R="3300"; I_des=1.5106; V_adc= 396.68 },
    @{ R="3881"; I_des=1.2850; V_adc= 356.60 },
    @{ R="5100"; I_des=0.9785; V_adc= 335.26 }
)

Write-Output "=== 3.6V Case 2 Points ==="
foreach ($p in $points_3v6) {
    $ideal_off = $p.V_adc - ($p.I_des * 186.428)
    "{0,5} ohm | I_des={1,7:F4} mA | V_adc={2,7:F2} mV | ImpliedOffset={3,6:F2} mV" -f $p.R, $p.I_des, $p.V_adc, $ideal_off
}

Write-Output "`n=== 5.0V Case 2 Points ==="
foreach ($p in $points_5v) {
    $ideal_off = $p.V_adc - ($p.I_des * 186.428)
    "{0,5} ohm | I_des={1,7:F4} mA | V_adc={2,7:F2} mV | ImpliedOffset={3,6:F2} mV" -f $p.R, $p.I_des, $p.V_adc, $ideal_off
}
