from smbus2 import SMBus, i2c_msg
import time


DEBUG = False   # True to echo every register write and config byte


# ADS122C04 Table 16 in Datasheet
ADS122C04_RESET_CMD        = 0x06     #0000 011x      Reset
ADS122C04_START_CMD        = 0x08     #0000 100x      Start/Sync
ADS122C04_POWERDOWN_CMD    = 0x02     #0000 001x      PowerDown
ADS122C04_RDATA_CMD        = 0x10     #0001 xxxx      RDATA
ADS122C04_RREG_CMD         = 0x20     #0010 rrxx      Read REG rr= register address 00 to 11
ADS122C04_WREG_CMD         = 0x40     #0100 rrxx      Write REG rr= register address 00 to 11

# ADS122C04 Table 16 in Datasheet
ADS122C04_CONFIG_0_REG = 0  # Configuration Register 0
ADS122C04_CONFIG_1_REG = 1  # Configuration Register 1
ADS122C04_CONFIG_2_REG = 2  # Configuration Register 2
ADS122C04_CONFIG_3_REG = 3  # Configuration Register 3

# Input Multiplexer Configuration
ADS122C04_MUX_AIN0_AIN1 = 0x0
ADS122C04_MUX_AIN0_AIN2 = 0x1
ADS122C04_MUX_AIN0_AIN3 = 0x2
ADS122C04_MUX_AIN1_AIN0 = 0x3
ADS122C04_MUX_AIN1_AIN2 = 0x4
ADS122C04_MUX_AIN1_AIN3 = 0x5
ADS122C04_MUX_AIN2_AIN3 = 0x6
ADS122C04_MUX_AIN3_AIN2 = 0x7
ADS122C04_MUX_AIN0_AVSS = 0x8
ADS122C04_MUX_AIN1_AVSS = 0x9
ADS122C04_MUX_AIN2_AVSS = 0xa
ADS122C04_MUX_AIN3_AVSS = 0xb
ADS122C04_MUX_REFPmREFN = 0xc
ADS122C04_MUX_AVDDmAVSS = 0xd
ADS122C04_MUX_SHORTED   = 0xe

# SBAS751B 8.3.2.2: for these four settings AINN is AVSS internally, the device
# bypasses and disables the PGA on its own whatever PGA_BYPASS says, and limits
# gain to 1, 2 or 4. Gains above 4 are silently reduced to 4.
ADS122C04_MUX_AVSS_REFERENCED = (0x8, 0x9, 0xa, 0xb)

#GAIN

ADS122C04_CONFIG_GAIN = {
    1 : 0x0,
    2 : 0x1,
    4 : 0x2,
    8 : 0x3,
    16: 0x4,
    32: 0x5,
    64: 0x6,
   128: 0x7
}

#PGA bypass
ADS122C04_PGA_DISABLED    = 0x1
ADS122C04_PGA_ENABLED     = 0x0

# Data Rate
# Turbo mode = Normal mode * 2 (Samples per Second)
# Normal mode
ADS122C04_DATA_RATE_20SPS   = 0x0
ADS122C04_DATA_RATE_45SPS   = 0x1
ADS122C04_DATA_RATE_90SPS   = 0x2
ADS122C04_DATA_RATE_175SPS  = 0x3
ADS122C04_DATA_RATE_330SPS  = 0x4
ADS122C04_DATA_RATE_600SPS  = 0x5
ADS122C04_DATA_RATE_1000SPS = 0x6

# Operating Mode
ADS122C04_OP_MODE_NORMAL = 0x0
ADS122C04_OP_MODE_TURBO  = 0x1

# samples per second each data rate setting gives in normal mode, turbo doubles them
ADS122C04_DATA_RATE_SPS = {
    ADS122C04_DATA_RATE_20SPS   : 20,
    ADS122C04_DATA_RATE_45SPS   : 45,
    ADS122C04_DATA_RATE_90SPS   : 90,
    ADS122C04_DATA_RATE_175SPS  : 175,
    ADS122C04_DATA_RATE_330SPS  : 330,
    ADS122C04_DATA_RATE_600SPS  : 600,
    ADS122C04_DATA_RATE_1000SPS : 1000,
}

# Conversion Mode
ADS122C04_CONVERSION_MODE_SINGLE_SHOT = 0x0
ADS122C04_CONVERSION_MODE_CONTINUOUS  = 0x1

# Voltage Reference Selection
ADS122C04_VREF_INTERNAL        = 0x0  #2.048V internal
ADS122C04_VREF_EXT_REF_PINS    = 0x1  #REFp and REFn external
ADS122C04_VREF_AVDD            = 0x2  #Analog Supply AVDD and AVSS

#Data Counter Enable
ADS122C04_DCNT_DISABLE     = 0x0
ADS122C04_DCNT_ENABLE      = 0x1

#tempareture enable
ADS122C04_TEMP_DISABLE = 0x0
ADS122C04_TEMP_ENABLE  = 0x1


#Integrity Check Enable
ADS122C04_CRC_DISABLED      =   0x0
ADS122C04_CRC_INVERTED      =   0x1
ADS122C04_CRC_CRC16_ENABLED =   0x2

#Burn-Out Current Source
ADS122C04_BURN_OUT_CURRENT_OFF = 0x0
ADS122C04_BURN_OUT_CURRENT_ON  = 0x1

#IDAC Current Setting
ADS122C04_IDAC_CURRENT_OFF     =  0x0
ADS122C04_IDAC_CURRENT_10_UA   =  0x1
ADS122C04_IDAC_CURRENT_50_UA   =  0x2
ADS122C04_IDAC_CURRENT_100_UA  =  0x3
ADS122C04_IDAC_CURRENT_250_UA  =  0x4
ADS122C04_IDAC_CURRENT_500_UA  =  0x5
ADS122C04_IDAC_CURRENT_1000_UA =  0x6
ADS122C04_IDAC_CURRENT_1500_UA =  0x7

#IDAC1 Routing Configuration
ADS122C04_IDAC1_DISABLED   =  0x0
ADS122C04_IDAC1_AIN0       =  0x1
ADS122C04_IDAC1_AIN1       =  0x2
ADS122C04_IDAC1_AIN2       =  0x3
ADS122C04_IDAC1_AIN3       =  0x4
ADS122C04_IDAC1_REFP       =  0x5
ADS122C04_IDAC1_REFN       =  0x6

#IDAC2-Routing Configuration
ADS122C04_IDAC2_DISABLED =   0x0
ADS122C04_IDAC2_AIN0     =   0x1
ADS122C04_IDAC2_AIN1     =   0x2
ADS122C04_IDAC2_AIN2     =   0x3
ADS122C04_IDAC2_AIN3     =   0x4
ADS122C04_IDAC2_REFP     =   0x5
ADS122C04_IDAC2_REFN     =   0x6

# 7 bit address is 100 followed by two bits per address pin, so A1 and A0 each pick
# one of four states rather than just high or low:
#   DGND = 00   DVDD = 01   SDA = 10   SCL = 11
# giving 0x40 (both DGND) through 0x4F (both SCL).
#
# There are two of these chips on the one I2C bus, and which one answers is the whole
# difference between a current reading and a rail reading:
#
#   0x40   current sense board, A1 and A0 to DGND. The amplifier output is on its AIN0.
#   0x45   voltage divider board, A1 and A0 to DVDD. The rail taps are on its AIN0,
#          AIN1 and AIN2, so AIN0 on this chip is nothing to do with AIN0 on 0x40.
#
# Ask for the one you mean, ADS122C04(ADS122C04_ADDRESS_VOLTAGE), rather than editing
# the default back and forth. currentMeasurementModule and voltageMeasurementModule
# each name their own. "i2cdetect -y 1" should show both.
#
# Historical note, because the comment here used to say 0x45 does not exist: v0.3 set
# this default to 0x45 while only the current board was wired, and every transfer
# failed with OSError errno 5, the NACK an empty address gives. That was the address
# being wrong for that board, not the address being invalid.
ADS122C04_ADDRESS_CURRENT = 0x40
ADS122C04_ADDRESS_VOLTAGE = 0x41
ADS122C04_ADDRESS = ADS122C04_ADDRESS_CURRENT   # default, kept for callers that
                                                # opened a chip without saying which
BIT_SHIFT_1 = 1
BIT_SHIFT_2 = 2
BIT_SHIFT_3 = 3
BIT_SHIFT_4 = 4
BIT_SHIFT_5 = 5
BIT_SHIFT_6 = 6
BIT_SHIFT_7 = 7


class ADS122C04 :
    def __init__(self, address=ADS122C04_ADDRESS, bus=1):
        """Create a I2c bus object.
        `address` is the 7 bit address the A1 and A0 straps select, see
        ADS122C04_ADDRESS above.
        """
        self.address = address
        self.bus = SMBus(bus)


    def read_register(self, register, read_bytes):
        cmd = ADS122C04_RREG_CMD + (register << BIT_SHIFT_2)
        write = i2c_msg.write(self.address, [cmd])
        read = i2c_msg.read(self.address, read_bytes)
        self.bus.i2c_rdwr(write, read)
        rlist = list(read)
       # print ("read register", rlist)
        return rlist

    def write_register(self, register, value):
        cmd = ADS122C04_WREG_CMD + (register << BIT_SHIFT_2)
        if DEBUG :
            print ("register", cmd, "value", value)
        write = i2c_msg.write(self.address, [cmd, value])  # register configurations with the WREG command (40h, 08h, 42h, 08h
        self.bus.i2c_rdwr(write)
    
    def send_cmd(self, cmd):
        write = i2c_msg.write(self.address, [cmd])  # register configurations with the WREG command (40h, 08h, 42h, 08h
        self.bus.i2c_rdwr(write)

    def read_data(self, bytes):
        read = i2c_msg.read(self.address, bytes)  # register configurations with the WREG command (40h, 08h, 42h, 08h
        self.bus.i2c_rdwr(read)
        rdata =  list(read)
        return rdata  
		
    def setup_config_register_0(self, pga_bypass_enable, gain, mux) :
        """Set the config register 0.
        """
        config = pga_bypass_enable + (gain << BIT_SHIFT_1) + (mux << BIT_SHIFT_4)
        self.write_register(ADS122C04_CONFIG_0_REG, config)
        if DEBUG :
            print(config)
        
        
    def setup_config_register_1(self, temp_sensor_enable, vref, cm, mode, data_rate) :
        """Set the config register 1.
        """
        config =  temp_sensor_enable + (vref << BIT_SHIFT_1) + (cm << BIT_SHIFT_3) + (mode << BIT_SHIFT_4) + (data_rate << BIT_SHIFT_5)
        self.write_register(ADS122C04_CONFIG_1_REG, config)
        if DEBUG :
            print(config)
        
    def setup_config_register_2(self, idac, bcs, crc, dcnt) :
        """Set the config register 2.
        """
        config = idac + (bcs << BIT_SHIFT_3) + (crc << BIT_SHIFT_4) + (dcnt << BIT_SHIFT_6)
        self.write_register(ADS122C04_CONFIG_2_REG, config)
        if DEBUG :
            print(config)
        
    def setup_config_register_3(self, i2mux, i1mux) :
        """Set the config register 3.
        """          
        config = (i2mux<< BIT_SHIFT_2) + (i1mux << BIT_SHIFT_5)
        self.write_register(ADS122C04_CONFIG_3_REG, config)
        if DEBUG :
            print(config) 
        
    def read_conversion_register(self) :
        """Read ADC conversion with conversation register.
         Returns the signed integer result of the read.
        """
        value = self.read_register(ADS122C04_CONFIG_2_REG, 1)
        #print (value)
        if value[0] & 0x80 :
            self.send_cmd(ADS122C04_RDATA_CMD)
            data = self.read_data(3)
           # print("list", data)
            raw = ((data[0] & 0xFF) << 16) + ((data[1] & 0xFF) << 8) + (data[2] & 0xFF)
            if raw & 0x800000 :          # 24 bit two's complement, sign extend
                raw = raw - 0x1000000    # or a code just below 0 V reads as +FS
            return raw >> 5              # drop the 5 noisy LSB, +FS becomes 262144
            #print ("value from ADC", test)
            #test = (test - READING_AT_0) * 35.355
            #print ("val", test)
            #return test

         
    def set_continuous_mode(self, mux=ADS122C04_MUX_AIN0_AVSS, gain=1,
                            pga_bypass=ADS122C04_PGA_ENABLED,
                            vref=ADS122C04_VREF_EXT_REF_PINS,
                            data_rate=ADS122C04_DATA_RATE_20SPS,
                            op_mode=ADS122C04_OP_MODE_NORMAL):
        """Set continuous mode for adc with mux, gain and data rate.
        The defaults reproduce the current sense configuration used since v0.1.

        Single ended inputs should pass pga_bypass=ADS122C04_PGA_DISABLED, because the
        PGA cannot take a common mode at AVSS. Worth knowing that for the AINx to AVSS
        mux settings the device does this for itself regardless of the bit, so passing it
        is the honest thing to write rather than the thing that makes it work.
        """
        if mux in ADS122C04_MUX_AVSS_REFERENCED and gain > 4 :
            print("gain", gain, "is unavailable with AINN = AVSS, the device will use 4")
        #set the config register 0 (0x40(1000 0000)
        self.setup_config_register_0(pga_bypass, ADS122C04_CONFIG_GAIN[gain], mux)
		
		#set the config register 1 (0x08 --> 0x00001000)
        cm = ADS122C04_CONVERSION_MODE_CONTINUOUS
        temp_sensor_enable = ADS122C04_TEMP_DISABLE
        self.setup_config_register_1(temp_sensor_enable, vref, cm, op_mode, data_rate)
		
		#set the config register 2 (0x42 --> 0100 0010)
        idac = ADS122C04_IDAC_CURRENT_OFF
        bcs  = ADS122C04_BURN_OUT_CURRENT_OFF  
        crc  = ADS122C04_CRC_DISABLED
        dcnt = ADS122C04_DCNT_DISABLE
        self.setup_config_register_2(idac, bcs, crc, dcnt)
		
		#set the config register 3 (0x08 --> 0000 1000)
        i2mux = ADS122C04_IDAC2_DISABLED
        i1mux = ADS122C04_IDAC1_DISABLED
        self.setup_config_register_3(i2mux, i1mux)

        #start conversion 
        self.send_cmd(ADS122C04_START_CMD)
    
    def send_reset_command(self):
        self.send_cmd(ADS122C04_RESET_CMD)
        self.send_cmd(ADS122C04_POWERDOWN_CMD)


    def read_conversion_when_ready(self, timeout=0.5, poll_interval=0.001):
        """Wait for the chip's own DRDY flag, then read that conversion.
        Returns the signed counts, or None if nothing became ready inside `timeout`.
        Faster than sleeping a fixed interval, and it never reads the same
        conversion twice, because reading the data clears DRDY.
        """
        deadline = time.monotonic() + timeout
        while True :
            value = self.read_conversion_register()
            if value is not None :
                return value
            if time.monotonic() >= deadline :
                return None
            time.sleep(poll_interval)


def conversion_period(data_rate, op_mode=ADS122C04_OP_MODE_NORMAL):
    """Seconds one conversion takes at this data rate, turbo mode included.
    """
    sps = ADS122C04_DATA_RATE_SPS[data_rate]
    if op_mode == ADS122C04_OP_MODE_TURBO :
        sps = sps*2
    return 1.0/sps


def read_conversions(adc_chip, count, discard=0, timeout=0.5,
                     poll_interval=0.001, retry_limit=3):
    """Read `count` conversions, taking each one as DRDY says it is ready and
    throwing the first `discard` away, the ones that straddle a settling input.
    Returns (samples, conversions that timed out).
    Raises RuntimeError when nothing ever became ready.
    """
    samples = []
    timeouts = 0
    skipped = 0
    while len(samples) < count and timeouts <= retry_limit :
        value = adc_chip.read_conversion_when_ready(timeout, poll_interval)
        if value is None :
            timeouts = timeouts + 1
        elif skipped < discard :
            skipped = skipped + 1
        else :
            samples.append(value)
    if not samples :
        raise RuntimeError("no ADC conversion became ready, check the I2C link to the ADS122C04")
    return samples, timeouts


def conversion_statistics(samples, clip_counts):
    """Compute summary statistics for a batch of conversion counts."""
    if not samples:
        raise ValueError("samples must not be empty")
    s_sorted = sorted(samples)
    n = len(s_sorted)
    mean = sum(samples) / float(n)
    median = s_sorted[n // 2]
    p10 = s_sorted[int(n * 0.1)]
    p90 = s_sorted[min(int(n * 0.9), n - 1)]
    minimum = s_sorted[0]
    maximum = s_sorted[-1]
    spread = maximum - minimum
    clipped = sum(1 for s in samples if s >= clip_counts)
    return {
        "mean": mean,
        "median": median,
        "p10": p10,
        "p90": p90,
        "minimum": minimum,
        "maximum": maximum,
        "spread": spread,
        "samples": n,
        "clipped": clipped,
    }


def average_ready_conversions(adc_chip, count, discard=0, timeout=0.5,
                              poll_interval=0.001, retry_limit=3):
    """Average `count` conversions, taking each one as DRDY says it is ready and
    throwing the first `discard` away, the ones that straddle a settling input.
    Returns (mean counts, samples used, conversions that timed out).
    Raises RuntimeError when nothing ever became ready.
    """
    samples, timeouts = read_conversions(adc_chip, count, discard, timeout,
                                         poll_interval, retry_limit)
    return sum(samples)/len(samples), len(samples), timeouts


def average_conversions(adc_chip, count, interval, retry_limit=3):
    """Average `count` conversions, skipping the reads the ADC was not ready for.
    Returns (mean counts, samples used, samples dropped).
    Raises RuntimeError when not one conversion completed.
    """
    samples = []
    dropped = 0
    while len(samples) < count and dropped < count * retry_limit :
        temp_data = adc_chip.read_conversion_register()
        if temp_data is None :
            dropped = dropped + 1
        else :
            samples.append(temp_data)
        time.sleep(interval)
    if not samples :
        raise RuntimeError("no ADC conversion completed, check the I2C link to the ADS122C04")
    return sum(samples)/len(samples), len(samples), dropped
