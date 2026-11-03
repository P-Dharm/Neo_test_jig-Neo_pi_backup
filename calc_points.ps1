$points = @(
    @{ R=690;  I_des=7.1429; I_meas=7.2331 },
    @{ R=910;  I_des=5.4348; I_meas=5.5395 },
    @{ R=1000; I_des=4.9505; I_meas=5.0253 },
    @{ R=3300; I_des=1.5106; I_meas=1.3098 },
    @{ R=3881; I_des=1.2850; I_meas=1.0948 },
    @{ R=5100; I_des=0.9785; I_meas=0.9803 }
)

foreach ($p in $points) {
    $v_adc = $p.I_meas * 186.428 + 152.50
    $ideal_offset = $v_adc - ($p.I_des * 186.428)
    $i_with_117 = ($v_adc - 117.78) / 186.428
    $err_with_152 = [Math]::Abs($p.I_meas - $p.I_des) / $p.I_des * 100.0
    $err_with_117 = [Math]::Abs($i_with_117 - $p.I_des) / $p.I_des * 100.0
    Write-Output ("{0,5} ohm | I_des={1,7:F4} mA | V_adc={2,7:F2} mV | IdealOff={3,6:F1} mV | Err(152.5)={4,5:F1}% | With117.78={5,7:F4} mA | Err(117.8)={6,5:F1}%" -f $p.R, $p.I_des, $v_adc, $ideal_offset, $err_with_152, $i_with_117, $err_with_117)
}
