$points = @(
    @{ R=690;    I_des=7.1429; I_meas=7.3813; V_adc=1500.95 },
    @{ R=910;    I_des=5.4348; I_meas=5.5380; V_adc=1184.931 },
    @{ R=1000;   I_des=4.9505; I_meas=4.9978; V_adc=1084.232 },
    @{ R=2000;   I_des=2.4876; I_meas=2.4164; V_adc= 602.991 },
    @{ R=2470;   I_des=2.0161; I_meas=1.9000; V_adc= 506.719 },
    @{ R=2910;   I_des=1.7123; I_meas=1.5681; V_adc= 444.834 },
    @{ R=2943;   I_des=1.6932; I_meas=1.4935; V_adc= 430.934 },
    @{ R=2959.9; I_des=1.6836; I_meas=1.4585; V_adc= 424.402 },
    @{ R=3010;   I_des=1.6556; I_meas=1.1921; V_adc= 374.750 },
    @{ R=3130;   I_des=1.5924; I_meas=1.2072; V_adc= 377.565 },
    @{ R=3300;   I_des=1.5106; I_meas=1.3119; V_adc= 397.076 },
    @{ R=3349.9; I_des=1.4881; I_meas=1.2798; V_adc= 391.092 },
    @{ R=3400;   I_des=1.4663; I_meas=1.2256; V_adc= 380.991 },
    @{ R=3520;   I_des=1.4164; I_meas=1.1669; V_adc= 370.041 },
    @{ R=3640;   I_des=1.3699; I_meas=1.1198; V_adc= 361.270 },
    @{ R=3803;   I_des=1.3113; I_meas=1.1043; V_adc= 358.372 },
    @{ R=3990;   I_des=1.2500; I_meas=1.0834; V_adc= 354.472 },
    @{ R=4110;   I_des=1.2136; I_meas=1.0694; V_adc= 351.867 },
    @{ R=4143;   I_des=1.2039; I_meas=1.0710; V_adc= 352.168 },
    @{ R=4159.9; I_des=1.1991; I_meas=1.0697; V_adc= 351.930 },
    @{ R=4210;   I_des=1.1848; I_meas=1.0680; V_adc= 351.606 },
    @{ R=5100;   I_des=0.9785; I_meas=0.9640; V_adc= 332.221 }
)

$gain = 18.6428
$rs = 10.0
$sens = $gain * $rs

Write-Output "=========================================================================================="
Write-Output ("{0,7} | {1,7} | {2,8} | {3,8} | {4,10} | {5,10}" -f "Res", "I_des", "I_meas", "V_adc", "ImpliedOff", "ErrOld%")
Write-Output "=========================================================================================="

foreach ($p in $points) {
    # Implied offset if Gain = 18.6428:
    # V_adc = I_des * Sens + Offset -> Offset = V_adc - I_des * Sens
    $implied_off = $p.V_adc - ($p.I_des * $sens)
    $err_old = [Math]::Abs($p.I_meas - $p.I_des) / $p.I_des * 100.0
    Write-Output ("{0,7} | {1,7:F4} | {2,8:F4} | {3,8:F2} | {4,10:F2} | {5,9:F2}%" -f `
        $p.R, $p.I_des, $p.I_meas, $p.V_adc, $implied_off, $err_old)
}
