# -*- coding: utf-8 -*-
"""
Created on Wed Nov 12 19:11:39 2025
@author: HP

TinySA Spectrum Analyzer Data Logger
Continuously logs RF sweeps to daily CSV files
"""
from tsapython import tinySA
import csv
import numpy as np
import time
import os
from datetime import datetime, date


def write_to_csv(base_dir, freq_arr, data_arr, current_time):
    """
    Writes one timestamped sweep row to today's CSV file.
    Creates a new file each day automatically.
    
    Returns:
        filename: Path to the CSV file written
    """
    if len(data_arr) == 0:
        print("WARNING: No data to write")
        return None
    
    today = date.today().strftime("%Y%m%d")
    filename = os.path.join(base_dir, f"{today}.csv")
    file_exists = os.path.isfile(filename)
    
    try:
        with open(filename, 'a', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            
            # Write header if new file
            if not file_exists:
                header = ["Timestamp"] + [f"{int(f)}" for f in freq_arr]
                writer.writerow(header)
            
            # Write data row
            row = [current_time.strftime("%H:%M:%S")] + [f"{p:.2f}" for p in data_arr]
            writer.writerow(row)
        
        return filename
        
    except Exception as e:
        print(f"ERROR writing to CSV: {e}")
        return None


def main():
    # Configuration
    base_dir = r"./tinysa_data"
    max_consecutive_errors = 5
    interval = 1
    
    # Create data directory
    os.makedirs(base_dir, exist_ok=True)
    
    # Initialize TinySA interface
    print("Initializing TinySA interface...")
    tsa = tinySA()
    tsa.set_verbose(True)
    tsa.set_error_byte_return(True)
    
    # Connect to device
    print("Attempting to connect to TinySA...")
    found, connected = tsa.autoconnect()
    
    if not connected:
        print("ERROR: Could not connect to TinySA.")
        print("Please check:")
        print("  - USB cable is connected")
        print("  - Device is powered on")
        print("  - Correct COM port permissions")
        return
    
    print("✓ Connected to TinySA successfully!")
    print(f"  Data directory: {base_dir}")
    print("\nStarting continuous logging...")
    print("Press Ctrl+C to stop.\n")
    
    consecutive_errors = 0
    sweep_count = 0
    next_sweep_time = time.time()
    
    try:
        while True:
            current_time = datetime.now()
            
            # Perform sweep
            try:
                consecutive_errors = 0  # Reset error counter on success
                
            except Exception as e:
                consecutive_errors += 1
                print(f"[{current_time.strftime('%H:%M:%S')}] Sweep error: {e}")
                
                if consecutive_errors >= max_consecutive_errors:
                    print(f"\nERROR: {max_consecutive_errors} consecutive sweep failures. Stopping.")
                    break
                
                time.sleep(interval)
                continue
            
            freq_arr, data_arr = tsa.frequencies().strip().splitlines(), tsa.data().strip().splitlines()
            freq_arr, data_arr = [int(item.decode('utf-8')) for item in freq_arr], [float(item.decode('utf-8')) for item in data_arr]
            
            if len(data_arr) == 0:
                print(f"[{current_time.strftime('%H:%M:%S')}] No valid data received, skipping...")
                time.sleep(interval)
                continue
            
            # Save to CSV
            filename = write_to_csv(base_dir, freq_arr, data_arr, current_time)
            
            if filename:
                sweep_count += 1
                avg_power = np.mean(data_arr)
                max_power = np.max(data_arr)
                print(f"[{current_time.strftime('%H:%M:%S')}] Sweep #{sweep_count}: "
                      f"{len(data_arr)} points | Avg: {avg_power:.1f} dBm | "
                      f"Max: {max_power:.1f} dBm | File: {os.path.basename(filename)}")
            
            # Precise timing - wait until next scheduled sweep
            next_sweep_time += interval
            sleep_time = max(0, next_sweep_time - time.time())
            time.sleep(sleep_time)
            
    except KeyboardInterrupt:
        print("\n\nStopping data logging...")
        print(f"Total sweeps completed: {sweep_count}")
        
    finally:
        # Clean shutdown
        print("Resuming TinySA display...")
        try:
            tsa.resume()
        except:
            pass
        
        print("Disconnecting...")
        try:
            tsa.disconnect()
        except:
            pass
        
        print(f"✓ All data saved to: {base_dir}")
        print("Goodbye!")


if __name__ == "__main__":
    main()
