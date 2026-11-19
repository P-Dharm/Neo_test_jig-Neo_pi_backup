- In isc/home of Rpi, run following git command 
```git clone https://github.com/P-Dharm/Neo_test_jig-Neo_pi_backup.git```
- Open ```jig_db.py``` and find ```JIG_ID``` and ```DEFAULT_JIG_ID``` and update it with JIG Number
- Open terminal and run 
    ```bash
    lsusb -v 2>/dev/null | grep -E "iSerial|iProduct|idVendor|idProduct"
    ```
    and find ```NordicSemiconductor Zephyr CMSIS-DAP```

    **Expected Output**
    ```bash
    idVendor           0x2fe3  NordicSemiconductor
    idProduct          0x0200  Zephyr CMSIS-DAP
    iProduct                2  Zephyr CMSIS-DAP
    iSerial                 3  E2E5D9AED9D33D64
    ```
- Open ```all_test_mux_remove.py``` and find ```CMSIS_DAP_VID_PID``` _(idProduct)_ and ```CMSIS_DAP_SERIAL``` _(iSerial)_ and verify if they are same from the terminal output. You may make changes if there are any. 
- Everything is configured. Cross-check the hardware wiring with ```PROJECT_GUIDE.md``` file.

**Note:** There is no changes required for supabase configuration apart from changing JIG_ID.