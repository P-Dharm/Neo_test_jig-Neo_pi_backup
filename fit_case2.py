# Case 2 points from NEO_test.xlsx (Voltage = 5V)
# Row 4:  5V, 570 ohm  -> I = 8.62 mA, Measured = 8.87 mA
# Row 5:  5V, 590 ohm  -> I = 8.33 mA, Measured = 8.69 mA
# Row 6:  5V, 690 ohm  -> I = 7.14 mA, Measured = 7.49 mA
# Row 7:  5V, 910 ohm  -> I = 5.43 mA, Measured = 5.71 mA
# Row 8:  5V, 1010 ohm -> I = 4.90 mA, Measured = 5.11 mA
# Row 9:  5V, 3313 ohm -> I = 1.50 mA, Measured = 1.49 mA
# Row 10: 5V, 3862 ohm -> I = 1.29 mA, Measured = 1.27 mA
# Row 11: 5V, 5100 ohm -> I = 0.98 mA, Measured = 1.16 mA

points = [
    (8.62, 8.87),
    (8.33, 8.69),
    (7.14, 7.49),
    (5.43, 5.71),
    (4.90, 5.11),
    (1.50, 1.49),
    (1.29, 1.27),
]

# For each point, reconstructed ADC voltage is:
# V_adc = Measured * Gain * Rs + Offset
# With Gain = 18.6428, Rs = 10.0, Offset = 117.78:
data = []
for i_true, i_meas in points:
    v_adc = i_meas * 18.6428 * 10.0 + 117.78
    data.append((i_true, v_adc))

# Linear regression on (i_true, v_adc):
# v_adc = slope * i_true + intercept
n = len(data)
sum_x = sum(d[0] for d in data)
sum_y = sum(d[1] for d in data)
sum_xx = sum(d[0]**2 for d in data)
sum_xy = sum(d[0]*d[1] for d in data)

slope = (n * sum_xy - sum_x * sum_y) / (n * sum_xx - sum_x**2)
intercept = (sum_y - slope * sum_x) / n

print(f"Slope (Sensitivity): {slope:.4f} mV/mA")
print(f"Intercept (Offset): {intercept:.4f} mV")
print(f"Gain (Slope / Rs=10): {slope / 10.0:.4f}")
