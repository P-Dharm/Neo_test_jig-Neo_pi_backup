# Let's inspect the exact values and see how each range behaves
import math

GAIN_HIGH = 224.47619
GAIN_MID = 18.6428
RS = 10.0
OFFSET_HIGH = 117.78
OFFSET_MID = 152.50

ADC_REF_VOLT = 2393.5
MAX_COUNT = 262144

# Desired currents from NEO_test.xlsx
test_points_5v = [
    (470, 10.417), (503, 9.747), (570, 8.621), (590, 8.333),
    (690, 7.143), (910, 5.435), (1010, 4.902), (3313, 1.505),
    (3862, 1.291), (5100, 0.9785), (10000, 0.500), (15100, 0.331),
    (33100, 0.151), (38200, 0.131), (43100, 0.116), (47000, 0.106)
]

test_points_3v6 = [
    (470, 7.500), (503, 7.018), (570, 6.207), (590, 6.000),
    (690, 5.143), (910, 3.913), (1010, 3.529), (3313, 1.083),
    (3862, 0.930), (5100, 0.7045), (10000, 0.360), (15100, 0.238),
    (33100, 0.109), (38200, 0.094), (43100, 0.084), (47000, 0.077)
]

print("--- 5V Test Points ---")
for r, i_mA in test_points_5v:
    v_high = i_mA * GAIN_HIGH * RS + OFFSET_HIGH
    v_mid = i_mA * GAIN_MID * RS + OFFSET_MID
    print(f"R={r:5d} | I={i_mA:7.4f} mA | Case3(V_high)={v_high:7.1f} mV | Case2(V_mid)={v_mid:7.1f} mV")

print("\n--- 3.6V Test Points ---")
for r, i_mA in test_points_3v6:
    v_high = i_mA * GAIN_HIGH * RS + OFFSET_HIGH
    v_mid = i_mA * GAIN_MID * RS + OFFSET_MID
    print(f"R={r:5d} | I={i_mA:7.4f} mA | Case3(V_high)={v_high:7.1f} mV | Case2(V_mid)={v_mid:7.1f} mV")
