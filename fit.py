# Let's collect all known Case 2 measurements from the log and user runs:
# Each point is: (Description, Desired_I_mA, Measured_ADC_mV)

points = [
    ("3.6V 5100 ohm", 0.7045, 258.796),
    ("3.6V 3862 ohm", 0.9298, 319.655), # or 3313 ohm? Let's check
    ("5.0V 5100 ohm", 0.9785, 334.898), # and 335.261
    ("5.0V 3862 ohm", 1.2913, 353.932),
]

# Let's do linear regression: V = I * (Gain * Rs) + Offset
# y = a * x + b where x = I (mA), y = V (mV)
# a = Gain * Rs = Gain * 10.0
# b = Offset (mV)

# Using points:
# (0.7045, 258.796)
# (0.9785, 334.898)
# Slope between 0.7045 and 0.9785:
delta_V = 334.898 - 258.796 # = 76.102 mV
delta_I = 0.9785 - 0.7045   # = 0.2740 mA
slope = delta_V / delta_I   # 76.102 / 0.2740 = 277.74 mV/mA !!
print(f"Slope between 0.7045 and 0.9785 mA = {slope:.3f} mV/mA")
print(f"Implied Gain = {slope / 10.0:.3f}")
offset = 334.898 - slope * 0.9785
print(f"Implied Offset = {offset:.3f} mV")
