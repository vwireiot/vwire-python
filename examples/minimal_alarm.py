"""
Minimal Alarm Script - Send a single alarm notification
=======================================================

This is the simplest possible script to send an alarm using Vwire IoT.
Replace the AUTH_TOKEN and DEVICE_ID with your actual values.

Usage: python minimal_alarm.py
"""

from vwire import Vwire, VwireConfig

# Replace with your actual values from the Vwire dashboard
AUTH_TOKEN = "YOUR_AUTH_TOKEN_HERE"
DEVICE_ID = "YOUR_DEVICE_ID_HERE"  # Required: VW-XXXXXX or VU-XXXXXX (system-generated)


def main():
    # Create device (uses secure TLS by default)
    device = Vwire(AUTH_TOKEN, DEVICE_ID)

    # Connect
    if not device.connect():
        print("Failed to connect!")
        return

    # Loop to send alarms or quit
    while True:
        user_input = input("\nEnter alarm message (or 'q' to quit): ").strip()
        
        if user_input.lower() == 'q':
            print("Exiting...")
            break
        
        if not user_input:
            print("Alarm message cannot be empty!")
            continue
        
        if device.alarm(user_input):
            print("✓ Alarm sent successfully!")
        else:
            print("✗ Failed to send alarm!")

    # Disconnect
    device.disconnect()

if __name__ == "__main__":
    main()