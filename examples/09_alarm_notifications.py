"""
Alarm & Notifications Example - Push Notifications and Alerts
==============================================================

This example demonstrates the notification features of Vwire IoT:
- notify(): Simple push notification to mobile app
- alarm(): Persistent alarm with sound that requires user acknowledgment
- email(): Send email notifications

These features mirror the Arduino library's notification capabilities.

Use Cases:
    - Temperature/humidity threshold alerts
    - Security system intrusion alarms
    - Equipment failure notifications
    - Periodic status reports via email

Note: Notification features require a paid plan (PRO, PRO+, ENTERPRISE).

Hardware: Any Python environment (Raspberry Pi, PC, etc.)
Platform: Vwire IoT (https://vwire.io)

Usage:
    python 09_alarm_notifications.py
"""

import time
import random
from datetime import datetime
from vwire import Vwire, VwireConfig

# =============================================================================
# CONFIGURATION
# =============================================================================


AUTH_TOKEN = "YOUR_AUTH_TOKEN_HERE"
DEVICE_ID = "YOUR_DEVICE_ID_HERE"  # Required: VW-XXXXXX or VU-XXXXXX (system-generated)
config = VwireConfig()

# Threshold settings for alerts
TEMP_HIGH_THRESHOLD = 25.0    # Celsius - trigger notification
TEMP_CRITICAL_THRESHOLD = 27.0  # Celsius - trigger alarm
HUMIDITY_LOW_THRESHOLD = 20.0  # Percent - trigger notification

# =============================================================================
# SIMULATED SENSORS
# =============================================================================

class SensorSimulator:
    """Simulates temperature and humidity sensors with occasional spikes."""
    
    def __init__(self):
        self.base_temp = 25.0
        self.base_humidity = 50.0
        self.spike_active = False
        self.spike_start = 0
    
    def read_temperature(self) -> float:
        """Read temperature with occasional high spikes for demo."""
        # Random spike - only 1% chance (about once per ~100 readings = ~3-4 minutes)
        # Reduced from 3% to avoid too-frequent alarms
        if not self.spike_active and random.random() < 0.01:
            self.spike_active = True
            self.spike_start = time.time()
        
        # Spike lasts 5 seconds (reduced from 10)
        if self.spike_active and (time.time() - self.spike_start) > 5:
            self.spike_active = False
        
        if self.spike_active:
            # Simulate temperature spike
            return round(self.base_temp + random.uniform(15, 20), 1)
        
        return round(self.base_temp + random.gauss(0, 2), 1)
    
    def read_humidity(self) -> float:
        """Read humidity."""
        return round(self.base_humidity + random.gauss(0, 5), 1)


# =============================================================================
# VWIRE CLIENT
# =============================================================================

device = Vwire(AUTH_TOKEN, DEVICE_ID, config=config)
sensors = SensorSimulator()

# Track last alert times to prevent spam
last_temp_alert = 0
last_humidity_alert = 0
last_critical_alert = 0
ALERT_COOLDOWN = 60  # Seconds between repeated alerts

# Track if timers have been set up (to prevent duplicates on reconnect)
timers_initialized = False

# =============================================================================
# MONITORING AND ALERT FUNCTIONS
# =============================================================================

def check_thresholds():
    """
    Read sensors and check for threshold violations.
    
    Demonstrates all three notification methods:
    - notify(): Simple push notification
    - alarm(): Critical alarm with persistent sound
    - email(): Email notification for logging/reports
    """
    global last_temp_alert, last_humidity_alert, last_critical_alert
    
    temp = sensors.read_temperature()
    humidity = sensors.read_humidity()
    current_time = time.time()
    
    # Send sensor data to dashboard
    device.virtual_send(0, temp)
    device.virtual_send(1, humidity)
    
    timestamp = datetime.now().strftime("%H:%M:%S")
    status = "OK"
    
    # Check for CRITICAL temperature (trigger alarm)
    if temp >= TEMP_CRITICAL_THRESHOLD:
        status = "🚨 CRITICAL"
        
        if current_time - last_critical_alert >= ALERT_COOLDOWN:
            last_critical_alert = current_time
            
            # Send persistent alarm - requires user to dismiss
            device.alarm(
                f"🚨 CRITICAL: Temperature {temp}°C exceeds {TEMP_CRITICAL_THRESHOLD}°C!",
                sound="alarm",  # Alarm sound file
                priority=3      # Critical priority
            )
            print(f"[ALARM] 🚨 Critical temperature alarm sent!")
            
            # Also send email for logging
            device.email(
                subject="🚨 CRITICAL: Temperature Alert",
                body=f"Temperature has reached {temp}°C at {timestamp}.\n"
                     f"This exceeds the critical threshold of {TEMP_CRITICAL_THRESHOLD}°C.\n"
                     f"Immediate action required!"
            )
            print(f"[EMAIL] Critical alert email sent!")
    
    # Check for HIGH temperature (trigger notification)
    elif temp >= TEMP_HIGH_THRESHOLD:
        status = "⚠️ WARNING"
        
        if current_time - last_temp_alert >= ALERT_COOLDOWN:
            last_temp_alert = current_time
            
            # Send push notification
            device.notify(
                f"⚠️ High temperature warning: {temp}°C"
            )
            print(f"[NOTIFY] ⚠️ High temperature notification sent!")
    
    # Check for LOW humidity
    if humidity <= HUMIDITY_LOW_THRESHOLD:
        status = "⚠️ WARNING" if status == "OK" else status
        
        if current_time - last_humidity_alert >= ALERT_COOLDOWN:
            last_humidity_alert = current_time
            
            device.notify(
                f"⚠️ Low humidity warning: {humidity}%"
            )
            print(f"[NOTIFY] ⚠️ Low humidity notification sent!")
    
    # Log reading
    print(f"[{timestamp}] Temp: {temp:5.1f}°C | Humidity: {humidity:4.1f}% | Status: {status}")


def send_daily_report():
    """
    Example: Send a daily summary email.
    
    In a real application, you'd accumulate statistics and send once per day.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    device.email(
        subject="📊 Daily Sensor Report",
        body=f"Daily sensor report generated at {timestamp}\n\n"
             f"Current readings:\n"
             f"  - Temperature: {sensors.read_temperature()}°C\n"
             f"  - Humidity: {sensors.read_humidity()}%\n\n"
             f"System status: Normal\n"
             f"Uptime: Running"
    )
    print(f"[EMAIL] 📊 Daily report email sent!")


# =============================================================================
# CONNECTION HANDLERS
# =============================================================================

@device.on_connected
def on_connected():
    """Setup monitoring when connected."""
    global timers_initialized
    
    print("[OK] Connected to Vwire server!")
    
    # Only set up timers once - prevents duplicates on reconnection
    if not timers_initialized:
        timers_initialized = True
        print()
        print("Monitoring started. Notifications will be sent when thresholds are exceeded.")
        print(f"  - High temp warning: >{TEMP_HIGH_THRESHOLD}°C (push notification)")
        print(f"  - Critical temp alarm: >{TEMP_CRITICAL_THRESHOLD}°C (alarm + email)")
        print(f"  - Low humidity warning: <{HUMIDITY_LOW_THRESHOLD}% (push notification)")
        print(f"  - Alert cooldown: {ALERT_COOLDOWN} seconds")
        print()
        
        # Check thresholds every 2 seconds
        device.timer.set_interval(2000, check_thresholds)
        
        # Send daily report every hour (for demo - in production use 24h)
        device.timer.set_interval(3600000, send_daily_report)
        
        # Initial check
        check_thresholds()
    else:
        print("  (Reconnected - timers already running)")


@device.on_disconnected
def on_disconnected():
    """Handle disconnection."""
    print("[WARN] Disconnected from server - will auto-reconnect...")


# =============================================================================
# STANDALONE EXAMPLES
# =============================================================================

def example_notify():
    """
    Example: Send a simple push notification.
    
    The notification appears on the user's mobile device.
    Similar to Arduino: Vwire.notify("message")
    """
    print("Sending push notification...")
    
    if device.notify("Hello from Python! This is a test notification."):
        print("[OK] Notification sent successfully!")
    else:
        print("[ERROR] Failed to send notification")


def example_alarm():
    """
    Example: Send a persistent alarm.
    
    The alarm:
    - Plays a sound on the mobile device
    - Shows a persistent notification
    - Requires user to acknowledge/dismiss
    
    Parameters:
    - message: Alarm text
    - sound: Sound file name (without extension)
    - priority: 1=normal, 2=high, 3=critical
    
    Similar to Arduino: Vwire.alarm("message", "sound", priority)
    """
    print("Sending alarm...")
    
    if device.alarm(
        message="🚨 Security Alert: Motion detected!",
        sound="alarm",
        priority=2
    ):
        print("[OK] Alarm sent successfully!")
    else:
        print("[ERROR] Failed to send alarm")


def example_email():
    """
    Example: Send an email notification.
    
    The email is sent to the device owner's registered email address.
    Similar to Arduino: Vwire.email("subject", "body")
    """
    print("Sending email...")
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if device.email(
        subject="Test Email from Vwire Python",
        body=f"This is a test email sent at {timestamp}.\n\n"
             f"Your IoT device is working correctly!"
    ):
        print("[OK] Email sent successfully!")
    else:
        print("[ERROR] Failed to send email")


def run_standalone_examples():
    """Run all notification examples once."""
    print("=" * 60)
    print("🔔 Vwire IoT - Notification Examples (Standalone)")
    print("=" * 60)
    print()
    
    print("Connecting...")
    if not device.connect():
        print("[ERROR] Failed to connect!")
        return
    
    print("[OK] Connected!")
    print()
    
    # Wait a moment for connection to stabilize
    time.sleep(1)
    
    # Run examples
    example_notify()
    time.sleep(2)
    
    example_alarm()
    time.sleep(2)
    
    example_email()
    time.sleep(2)
    
    device.disconnect()
    print()
    print("[OK] Done! Check your mobile app and email for notifications.")


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 60)
    print("🔔 Vwire IoT - Alarm & Notifications Example")
    print("=" * 60)
    print()
    print("Virtual Pins:")
    print("  V0: Temperature (gauge)")
    print("  V1: Humidity (gauge)")
    print()
    print(f"Server: {config.server}:{config.port}")
    print()
    print("Note: Notifications require a paid plan (PRO, PRO+, ENTERPRISE)")
    print()
    
    # Connect
    print("Connecting...")
    if not device.connect():
        print("[ERROR] Failed to connect!")
        return
    
    print()
    print("Monitoring sensor data... Press Ctrl+C to stop")
    print("-" * 60)
    
    try:
        device.run()
        
    except KeyboardInterrupt:
        print("\n\n[STOP] Stopping...")
    finally:
        device.disconnect()
        print("[OK] Disconnected.")


if __name__ == "__main__":
    import sys
    
    # Check for standalone examples flag
    if len(sys.argv) > 1 and sys.argv[1] == "--examples":
        run_standalone_examples()
    else:
        main()
