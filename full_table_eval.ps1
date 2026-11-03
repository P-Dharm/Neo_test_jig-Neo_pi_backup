$table = @(
    @{ V=5; R=690;    I_des=7.1429; I_meas=7.3813; V_adc=1500.95 },
    @{ V=5; R=910;    I_des=5.4348; I_meas=5.5380; V_adc=1184.931 },
    @{ V=5; R=1000;   I_des=4.9505; I_meas=4.9978; V_adc=1084.232 },
    @{ V=5; R=2000;   I_des=2.4876; I_meas=2.4164; V_adc= 602.991 },
    @{ V=5; R=2470;   I_des=2.0161; I_meas=1.9000; V_adc= 506.719 },
    @{ V=5; R=2910;   I_des=1.7123; I_meas=1.5681; V_adc= 444.834 },
    @{ V=5; R=2943;   I_des=1.6932; I_meas=1.4935; V_adc= 430.934 },
    @{ V=5; R=2959.9; I_des=1.6836; I_meas=1.4585; V_adc= 424.402 },
    @{ V=5; R=3010;   I_des=1.6556; I_meas=1.1921; V_adc= 374.750 },
    @{ V=5; R=3130;   I_des=1.5924; I_meas=1.2072; V_adc= 377.565 },
    @{ V=5; R=3300;   I_des=1.5106; I_meas=1.3119; V_adc= 397.076 },
    @{ V=5; R=3349.9; I_des=1.4881; I_meas=1.2798; V_adc= 391.092 },
    @{ V=5; R=3400;   I_des=1.4663; I_meas=1.2256; V_adc= 380.991 },
    @{ V=5; R=3520;   I_des=1.4164; I_meas=1.1669; V_adc= 370.041 },
    @{ V=5; R=3640;   I_des=1.3699; I_meas=1.1198; V_adc= 361.270 },
    @{ V=5; R=3803;   I_des=1.3113; I_meas=1.1043; V_adc= 358.372 },
    @{ V=5; R=3990;   I_des=1.2500; I_meas=1.0834; V_adc= 354.472 },
    @{ V=5; R=4110;   I_des=1.2136; I_meas=1.0694; V_adc= 351.867 },
    @{ V=5; R=4143;   I_des=1.2039; I_meas=1.0710; V_adc= 352.168 },
    @{ V=5; R=4159.9; I_des=1.1991; I_meas=1.0697; V_adc= 351.930 },
    @{ V=5; R=4210;   I_des=1.1848; I_meas=1.0680; V_adc= 351.606 },
    @{ V=5; R=5100;   I_des=0.9785; I_meas=0.9640; V_adc= 332.221 }
)

$nodes = @(
    @{ V=   0.0; Off=150.0 },
    @{ V= 332.0; Off=150.0 },
    @{ V= 352.0; Off=128.0 },
    @{ V= 365.0; Off=108.0 },
    @{ V= 430.0; Off=114.0 },
    @{ V= 550.0; Off=135.0 },
    @{ V=1150.0; Off=170.0 },
    @{ V=2500.0; Off=170.0 }
)

function Get-Calibrated-Offset($v) {
    if ($v -le $nodes[0].V) { return $nodes[0].Off }
    if ($v -ge $nodes[-1].V) { return $nodes[-1].Off }
    for ($i = 0; $i -lt ($nodes.Count - 1); $i++) {
        if ($v -ge $nodes[$i].V -and $v -le $nodes[$i+1].V) {
            $v0 = $nodes[$i].V
            $v1 = $nodes[$i+1].V
            $o0 = $nodes[$i].Off
            $o1 = $nodes[$i+1].Off
            $t = ($v - $v0) / ($v1 - $v0)
            return $o0 + $t * ($o1 - $o0)
        }
    }
    return 152.50
}

$sens = 18.6428 * 10.0

Write-Output "=========================================================================================================="
Write-Output ("{0,7} | {1,7} | {2,8} | {3,8} | {4,7} | {5,8} | {6,8} | {7,7}" -f "Res", "I_des", "V_adc", "OldMeas", "OldErr%", "DynOff", "NewMeas", "NewErr%")
Write-Output "=========================================================================================================="

foreach ($p in $table) {
    $v = $p.V_adc
    $off = Get-Calibrated-Offset $v
    $new_meas = ($v - $off) / $sens
    $new_err = [Math]::Abs($new_meas - $p.I_des) / $p.I_des * 100.0
    $old_err = [Math]::Abs($p.I_meas - $p.I_des) / $p.I_des * 100.0

    Write-Output ("{0,7} | {1,7:F4} | {2,8:F2} | {3,8:F4} | {4,6:F2}% | {5,7:F2} | {6,8:F4} | {7,6:F2}%" -f `
        $p.R, $p.I_des, $v, $p.I_meas, $old_err, $off, $new_meas, $new_err)
}
