$points = @(
    @{ R=690;    I_des=7.1429; V_adc=1500.95 },
    @{ R=910;    I_des=5.4348; V_adc=1184.931 },
    @{ R=1000;   I_des=4.9505; V_adc=1084.232 },
    @{ R=2000;   I_des=2.4876; V_adc= 602.991 },
    @{ R=2470;   I_des=2.0161; V_adc= 506.719 },
    @{ R=2910;   I_des=1.7123; V_adc= 444.834 },
    @{ R=2943;   I_des=1.6932; V_adc= 430.934 },
    @{ R=2959.9; I_des=1.6836; V_adc= 424.402 },
    @{ R=3300;   I_des=1.5106; V_adc= 397.076 },
    @{ R=3349.9; I_des=1.4881; V_adc= 391.092 },
    @{ R=3400;   I_des=1.4663; V_adc= 380.991 },
    @{ R=3520;   I_des=1.4164; V_adc= 370.041 },
    @{ R=3640;   I_des=1.3699; V_adc= 361.270 },
    @{ R=3803;   I_des=1.3113; V_adc= 358.372 },
    @{ R=3990;   I_des=1.2500; V_adc= 354.472 },
    @{ R=4110;   I_des=1.2136; V_adc= 351.867 },
    @{ R=4143;   I_des=1.2039; V_adc= 352.168 },
    @{ R=4159.9; I_des=1.1991; V_adc= 351.930 },
    @{ R=4210;   I_des=1.1848; V_adc= 351.606 },
    @{ R=5100;   I_des=0.9785; V_adc= 332.221 }
)

$gain = 18.6428
$rs = 10.0
$sens = $gain * $rs

# Piecewise linear offset spline based on empirical nodes:
# Node 1: (  0.0, 150.0)
# Node 2: (332.0, 150.0)
# Node 3: (352.0, 128.0)
# Node 4: (365.0, 108.0)
# Node 5: (430.0, 114.0)
# Node 6: (550.0, 135.0)
# Node 7: (1150.0, 170.0)
# Node 8: (2500.0, 170.0)

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

Write-Output "=========================================================================================="
Write-Output ("{0,7} | {1,7} | {2,8} | {3,8} | {4,8} | {5,7} | {6,7}" -f "Res", "I_des", "V_adc", "CalOff", "NewMeas", "NewErr%", "OldErr%")
Write-Output "=========================================================================================="

foreach ($p in $points) {
    $v = $p.V_adc
    $off = Get-Calibrated-Offset $v
    $new_meas = ($v - $off) / $sens
    $new_err = [Math]::Abs($new_meas - $p.I_des) / $p.I_des * 100.0
    
    # Old calculation with fixed 152.50
    $old_meas = ($v - 152.50) / $sens
    $old_err = [Math]::Abs($old_meas - $p.I_des) / $p.I_des * 100.0

    Write-Output ("{0,7} | {1,7:F4} | {2,8:F2} | {3,8:F2} | {4,8:F4} | {5,6:F2}% | {6,6:F2}%" -f `
        $p.R, $p.I_des, $v, $off, $new_meas, $new_err, $old_err)
}
