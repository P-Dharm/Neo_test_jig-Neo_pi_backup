# Power measurement firmware

One pass of the jig's electrical checks: the DUT's current draw and its three
supply rails, from one `python3 main.py`.

    python3 main.py                     current, then the three rails
    python3 main.py --current           current only
    python3 main.py --voltage           rails only, what voltage_main.py used to do
    python3 main.py --profile careful   slower rail sampling, rejects mains hum
    python3 main.py --verbose           show the module chatter as well

Exit code is 0 when everything is inside spec, 1 when it is not, 2 on a usage
error, so a jig script can act on it.

## What is here

| file | |
| --- | --- |
| `main.py` | the merged entry point, current and rails in one report |
| `currentMeasurementModule.py` | current, with the auto range search and the per range calibration |
| `voltageMeasurementModule.py` | the three rails, dividers, pin drops and accept windows |
| `ADS122C04.py` | the I2C driver both modules share |

## Two chips, one bus

| address | board | what is on it |
| --- | --- | --- |
| `0x40` | current sense | the amplifier output, on its AIN0, PGA in circuit |
| `0x45` | voltage divider | the rail taps, on its AIN0, AIN1 and AIN2 |

Each module opens its own, `ADS122C04_ADDRESS_CURRENT` and
`ADS122C04_ADDRESS_VOLTAGE` in `ADS122C04.py`, so nothing has to be edited between
a current run and a rail run. AIN0 on one chip has nothing to do with AIN0 on the
other. `i2cdetect -y 1` should show both.

Current is measured first, because when the shunt sits in the DUT ground return the
rail readings have to have the drop across it taken off, see
`SHUNT_IN_GROUND_RETURN`.

## Where the numbers to check live

- rail dividers and windows: the resistor, `PIN_DROP_*` and `MIN_*`/`MAX_*` blocks
  at the top of `voltageMeasurementModule.py`. Measure a tap with
  `dividerTest.py --calibrate` in `Auto_gain_adjustment_firmware` rather than
  trusting the sheet.
- current calibration: `ADC_REF_VOLT`, the `OFFSET_*` constants and `EQUIVALENT_RS`
  at the top of `currentMeasurementModule.py`, fitted by `calibrate.py` in
  `Auto_gain_adjustment_firmware`.
- rail sampling speed: `PROFILES` in `voltageMeasurementModule.py`.

## The relationship with Auto_gain_adjustment_firmware

The three modules here are copies. That folder keeps its own set along with the
bench tools that are not part of a jig run: `dividerTest.py`, `calibrate.py`,
`rawRead.py` and `adcDiag.py`. Anything learned on the bench with those has to be
copied into the constants here, in this folder's modules, or the jig will not see
it.
