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

# Let's inspect the offset needed for each point:
# Offset = V_adc - I_des * 186.428
Write-Output "--- Offset vs V_adc ---"
foreach ($p in ($points | Sort-Object { [double]$_.V_adc })) {
    $off = $p.V_adc - ($p.I_des * 186.428)
    "{0,8:F2} mV (I={1,6:F4} mA, R={2,6}) -> Required Offset = {3,6:F2} mV" -f $p.V_adc, $p.I_des, $p.R, $off
}
