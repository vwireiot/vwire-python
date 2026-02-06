"""
Vwire Core Module - Main client implementation

This module provides the core Vwire client class following the same
architecture as the Arduino Vwire library: single-threaded with explicit
run() calls for MQTT processing.

Arduino Library Equivalents:
    Python                  Arduino
    ------                  -------
    Vwire(token)           Vwire.config(token)
    connect()              Vwire.begin(ssid, password)
    run()                  Vwire.run()
    connected              Vwire.connected()
    virtual_send(pin, v)   Vwire.virtualSend(pin, v)
    sync_virtual(pin)      Vwire.syncVirtual(pin)
    sync_all()             Vwire.syncAll()
    notify(msg)            Vwire.notify(msg)
    email(subj, body)      Vwire.email(subj, body)
    log(msg)               Vwire.log(msg)
"""

import ssl
import time
import json
import random
import logging
from typing import Any, Callable, Dict, List, Optional, Union
from enum import Enum

import paho.mqtt.client as mqtt

from .config import VwireConfig
from .timer import VwireTimer

# Setup logging
logger = logging.getLogger("vwire")

# Type aliases
PinValue = Union[int, float, str, List[Any]]
PinHandler = Callable[[Any], None]

# Check paho-mqtt version for API compatibility
PAHO_V2 = hasattr(mqtt, 'CallbackAPIVersion')


class ConnectionState(Enum):
    """Connection state enumeration."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"


class Vwire:
    """
    Vwire IoT Platform Client.
    
    Single-threaded implementation matching Arduino library architecture.
    Call run() in your main loop to process MQTT messages and timers.
    
    Supports device ID-based routing for better organization. Use device_id
    parameter or set_device_id() method to specify custom device identifiers
    for MQTT topics instead of using auth tokens directly.
    
    Note on server/port configuration:
        Like Arduino, server and port are internal settings configured via
        VwireConfig. The client uses secure defaults (mqtt.vwire.io:8883 TLS).
        Use VwireConfig factory methods for custom configurations.
    
    Example:
        # Basic usage with auth token as device ID
        device = Vwire("your_auth_token")
        
        # Custom device ID for better organization
        device = Vwire("your_auth_token", device_id="sensor-001")
        
        @device.on_virtual_receive(0)
        def handle_v0(value):
            print(f"V0 = {value}")
        
        if device.connect():
            device.run()  # Blocks forever
        
        # Send notifications (paid plans only)
        device.notify("Sensor alert!")
        device.email("Alert", "Temperature exceeded threshold")
    """
    
    # Event types
    VIRTUAL_RECEIVE = "virtual_receive"
    VIRTUAL_READ = "virtual_read"
    
    def __init__(self, auth_token: str, device_id: str, config: Optional[VwireConfig] = None):
        """
        Initialize Vwire client.
        
        Equivalent to Arduino's Vwire.config(authToken) + Vwire.setDeviceId(deviceId).
        
        Args:
            auth_token: Device authentication token from dashboard
            device_id: Device ID for MQTT topics (VW-XXXXXX or VU-XXXXXX, system-generated)
            config: Optional configuration (uses secure defaults if not provided)
        """
        self._auth_token = auth_token
        self._device_id = device_id  # Required: system-generated device ID for topics
        self._config = config or VwireConfig()
        
        # Setup logging
        if self._config.debug:
            logging.basicConfig(level=logging.DEBUG)
            logger.setLevel(logging.DEBUG)
        
        # Create MQTT client
        self._setup_mqtt_client()
        
        # State management
        self._state = ConnectionState.DISCONNECTED
        self._pin_values: Dict[str, PinValue] = {}
        self._handlers: Dict[str, Dict[int, Callable]] = {
            self.VIRTUAL_RECEIVE: {},
            self.VIRTUAL_READ: {},
        }
        
        # Callbacks
        self._on_connected_callback: Optional[Callable] = None
        self._on_disconnected_callback: Optional[Callable] = None
        
        # Timer for scheduled tasks (runs in main thread via run())
        self._timer = VwireTimer()
        
        # Internal state
        self._reconnect_count = 0
        self._last_reconnect_attempt = 0.0
        self._stop_requested = False
        self._last_disconnect_time = 0.0
        self._disconnects_in_window = 0
        
        logger.debug(f"Vwire client initialized: {self._config}")
    
    def _setup_mqtt_client(self) -> None:
        """Setup MQTT client matching Arduino library approach."""
        transport = "websockets" if self._config.use_websocket else "tcp"
        
        # Client ID: "vwire-py-{device_id}" to avoid conflicts with other clients
        # Using device_id instead of auth_token ensures each device has a unique client_id
        # The "-py" suffix differentiates Python clients from Arduino clients
        client_id = f"vwire-py-{self._device_id}"
        
        if PAHO_V2:
            self._mqtt = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
                client_id=client_id,
                protocol=mqtt.MQTTv311,
                transport=transport,
                clean_session=True
            )
        else:
            self._mqtt = mqtt.Client(
                client_id=client_id,
                protocol=mqtt.MQTTv311,
                transport=transport,
                clean_session=True
            )
        
        # WebSocket path
        if self._config.use_websocket:
            self._mqtt.ws_set_options(path="/mqtt")
        
        # Authentication (token as both username and password like Arduino)
        self._mqtt.username_pw_set(
            username=self._auth_token,
            password=self._auth_token
        )
        
        # TLS configuration
        if self._config.use_tls:
            self._setup_tls()

        # Last will so dashboard reflects offline state if connection drops
        will_topic = f"vwire/{self._device_id}/status"
        self._mqtt.will_set(will_topic, payload='{"status":"offline"}', qos=1, retain=True)
        
        # Callbacks
        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_disconnect = self._on_disconnect
        self._mqtt.on_message = self._on_message
    
    def _setup_tls(self) -> None:
        """Configure TLS settings."""
        cert_reqs = ssl.CERT_REQUIRED if self._config.verify_ssl else ssl.CERT_NONE
        
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = self._config.verify_ssl
        ssl_context.verify_mode = cert_reqs
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        
        if self._config.ca_certs:
            ssl_context.load_verify_locations(cafile=self._config.ca_certs)
        else:
            ssl_context.load_default_certs()
        
        if self._config.client_cert and self._config.client_key:
            ssl_context.load_cert_chain(
                certfile=self._config.client_cert,
                keyfile=self._config.client_key
            )
        
        self._mqtt.tls_set_context(ssl_context)
    
    # ========== Connection Methods ==========
    
    def connect(self, timeout: int = 30) -> bool:
        """
        Connect to the Vwire server.
        
        Equivalent to Arduino's begin() method.
        Starts a background thread for MQTT operations so messages
        are sent immediately without needing manual loop() calls.
        
        Args:
            timeout: Connection timeout in seconds
            
        Returns:
            True if connected successfully
        """
        if self._state == ConnectionState.CONNECTED:
            return True
        
        self._state = ConnectionState.CONNECTING
        logger.info(f"Connecting to {self._config.server}:{self._config.port}...")
        
        try:
            self._mqtt.connect(
                self._config.server,
                self._config.port,
                keepalive=self._config.keepalive
            )
            
            # Start background thread for MQTT operations
            # This ensures messages are sent immediately without manual loop() calls
            self._mqtt.loop_start()
            
            # Wait for connection to complete
            start = time.time()
            while self._state == ConnectionState.CONNECTING:
                time.sleep(0.1)
                if (time.time() - start) > timeout:
                    logger.error("Connection timeout")
                    self._mqtt.loop_stop()
                    self._state = ConnectionState.DISCONNECTED
                    return False
            
            return self._state == ConnectionState.CONNECTED
            
        except Exception as e:
            logger.error(f"Connection error: {e}")
            self._state = ConnectionState.DISCONNECTED
            return False
    
    def disconnect(self) -> None:
        """Disconnect from the Vwire server."""
        if self._state == ConnectionState.DISCONNECTED:
            return
        
        logger.info("Disconnecting...")
        self._stop_requested = True
        self._timer.stop()
        
        try:
            status_topic = f"vwire/{self._device_id}/status"
            self._mqtt.publish(status_topic, '{"status":"offline"}', qos=1, retain=True)
            time.sleep(0.2)  # Allow message to be sent
            self._mqtt.disconnect()
            self._mqtt.loop_stop()  # Stop background thread
        except Exception:
            pass
        
        self._state = ConnectionState.DISCONNECTED
    
    def loop(self, timeout: float = 0.1) -> None:
        """
        Process timers manually (optional).
        
        Note: MQTT messages are processed automatically in a background thread.
        This method only needs to be called if using VwireTimer for scheduled tasks
        without using run().
        
        Args:
            timeout: Unused, kept for API compatibility
        """
        # MQTT is handled by background thread (loop_start)
        # Only process timers here
        self._timer.run()
    
    def run(self) -> None:
        """
        Main loop - blocks forever, processing MQTT and timers.
        
        This matches the Arduino pattern where you call run() in loop().
        """
        self._stop_requested = False
        
        try:
            while not self._stop_requested:
                self._run_once()
                time.sleep(0.01)
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            self.disconnect()
    
    def _run_once(self) -> None:
        """
        Single iteration - process timers and check connection.
        
        MQTT is handled by background thread; this handles timers and reconnection.
        """
        # If connected, just process timers (MQTT handled by background thread)
        if self._mqtt.is_connected():
            self._timer.run()
            return
        
        # Handle disconnection state change
        if self._state == ConnectionState.CONNECTED:
            self._state = ConnectionState.DISCONNECTED
            logger.warning("Connection lost")
            if self._on_disconnected_callback:
                try:
                    self._on_disconnected_callback()
                except Exception as e:
                    logger.error(f"Error in disconnect callback: {e}")
        
        # Auto reconnect
        if self._config.max_reconnect_attempts == 0 or \
           self._reconnect_count < self._config.max_reconnect_attempts:
            now = time.time()
            if now - self._last_reconnect_attempt >= self._config.reconnect_interval:
                self._last_reconnect_attempt = now
                self._reconnect_count += 1
                logger.info(f"Reconnecting (attempt {self._reconnect_count})...")
                if self.connect(timeout=10):
                    self._reconnect_count = 0
    
    @property
    def connected(self) -> bool:
        """Check if connected to server."""
        return self._state == ConnectionState.CONNECTED and self._mqtt.is_connected()
    
    @property
    def timer(self) -> VwireTimer:
        """Get the built-in timer instance."""
        return self._timer
    
    # ========== Virtual Pin Operations ==========
    
    def virtual_send(self, pin: int, *values: Any) -> bool:
        """
        Send value(s) to a virtual pin.
        
        Matches Arduino library Vwire.virtualSend(): publishes raw value to
        vwire/{token}/pin/V{pin} with comma-separated payloads for multiple values.
        """
        if not self.connected:
            logger.warning("Cannot write: not connected")
            return False

        if len(values) == 0:
            logger.warning("Cannot write: no values provided")
            return False

        if len(values) == 1:
            payload = self._format_value(values[0])
        else:
            payload = ",".join(self._format_value(v) for v in values)

        topic = f"vwire/{self._device_id}/pin/V{pin}"
        # Use QoS 1 for reliable delivery (guaranteed at least once)
        result = self._mqtt.publish(topic, payload, qos=1)
        return result.rc == mqtt.MQTT_ERR_SUCCESS

    def _format_value(self, value: Any) -> str:
        """Format value similar to Arduino VirtualPin: bool->1/0, numbers->string."""
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, float):
            text = f"{value:.4f}".rstrip("0").rstrip(".")
            return text or "0"
        return str(value)
    
    def virtual_read(self, pin: int) -> Optional[PinValue]:
        """Get the last known value of a virtual pin."""
        return self._pin_values.get(f"V{pin}")
    
    def sync_virtual(self, pin: int) -> bool:
        """Request sync of a virtual pin value from server."""
        if not self.connected:
            return False
        
        topic = f"vwire/{self._device_id}/sync/V{pin}"
        result = self._mqtt.publish(topic, "", qos=1)
        return result.rc == mqtt.MQTT_ERR_SUCCESS
    
    def sync_all(self) -> bool:
        """Request sync of all pin values from server."""
        if not self.connected:
            return False
        
        topic = f"vwire/{self._device_id}/sync"
        result = self._mqtt.publish(topic, "", qos=1)
        return result.rc == mqtt.MQTT_ERR_SUCCESS
    
    # ========== Notifications (Arduino library equivalent methods) ==========
    
    def notify(self, message: str) -> bool:
        """
        Send push notification to device owner.
        
        Equivalent to Arduino's Vwire.notify(message).
        Note: Only available for paid plans (PRO, PRO+, ENTERPRISE).
        
        Args:
            message: Notification text
            
        Returns:
            True if message was sent successfully
        """
        if not self.connected:
            logger.warning("Cannot send notification: not connected")
            return False
        
        topic = f"vwire/{self._device_id}/notify"
        result = self._mqtt.publish(topic, message, qos=1)
        return result.rc == mqtt.MQTT_ERR_SUCCESS
    
    def alarm(self, message: str, sound: str = "default", priority: int = 1) -> bool:
        """
        Send alarm notification with persistent sound/vibration.
        
        Equivalent to Arduino's Vwire.alarm(message, sound, priority).
        Triggers persistent alarm on mobile device that requires user interaction.
        Note: Only available for paid plans (PRO, PRO+, ENTERPRISE).
        
        Args:
            message: Alarm message text
            sound: Sound file name (without .mp3 extension)
            priority: Priority level (1=normal, 2=high, 3=critical)
            
        Returns:
            True if alarm was sent successfully
        """
        if not self.connected:
            logger.warning("Cannot send alarm: not connected")
            return False
        
        # Generate unique alarm ID
        alarm_id = f"alarm_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
        
        alarm_data = {
            "type": "alarm",
            "message": message,
            "alarmId": alarm_id,
            "sound": sound,
            "priority": priority,
            "timestamp": int(time.time() * 1000)
        }
        
        topic = f"vwire/{self._device_id}/alarm"
        payload = json.dumps(alarm_data)
        result = self._mqtt.publish(topic, payload, qos=1)
        return result.rc == mqtt.MQTT_ERR_SUCCESS
    
    def email(self, subject: str, body: str) -> bool:
        """
        Send email notification to device owner.
        
        Equivalent to Arduino's Vwire.email(subject, body).
        Note: Only available for paid plans (PRO, PRO+, ENTERPRISE).
        
        Args:
            subject: Email subject
            body: Email body text
            
        Returns:
            True if message was sent successfully
        """
        if not self.connected:
            logger.warning("Cannot send email: not connected")
            return False
        
        import json
        payload = json.dumps({"subject": subject, "body": body})
        topic = f"vwire/{self._device_id}/email"
        result = self._mqtt.publish(topic, payload, qos=1)
        return result.rc == mqtt.MQTT_ERR_SUCCESS
    
    def log(self, message: str) -> bool:
        """
        Send log message to server.
        
        Equivalent to Arduino's Vwire.log(message).
        Useful for remote debugging and monitoring.
        
        Args:
            message: Log text
            
        Returns:
            True if message was sent successfully
        """
        if not self.connected:
            logger.warning("Cannot send log: not connected")
            return False
        
        topic = f"vwire/{self._device_id}/log"
        result = self._mqtt.publish(topic, message, qos=1)
        return result.rc == mqtt.MQTT_ERR_SUCCESS
    
    # ========== Event Handlers ==========
    
    def on_virtual_receive(self, pin: int) -> Callable:
        """
        Decorator to register a handler for receiving data on a virtual pin.
        
        Matches Arduino's VWIRE_RECEIVE(pin) macro.
        
        Example:
            @device.on_virtual_receive(0)
            def handle_v0(value):
                print(f"V0 received: {value}")
        """
        def decorator(func: PinHandler) -> PinHandler:
            self._handlers[self.VIRTUAL_RECEIVE][pin] = func
            return func
        return decorator
    
    def on_virtual_read(self, pin: int) -> Callable:
        """Decorator to register a virtual read handler."""
        def decorator(func: PinHandler) -> PinHandler:
            self._handlers[self.VIRTUAL_READ][pin] = func
            return func
        return decorator
    
    @property
    def on_connected(self) -> Callable:
        """Decorator to register connection handler."""
        def decorator(func: Callable) -> Callable:
            self._on_connected_callback = func
            return func
        return decorator
    
    @property
    def on_disconnected(self) -> Callable:
        """Decorator to register disconnection handler."""
        def decorator(func: Callable) -> Callable:
            self._on_disconnected_callback = func
            return func
        return decorator
    
    # ========== MQTT Callbacks ==========
    
    def _on_connect(self, client, userdata, flags, rc):
        """Handle MQTT connection."""
        if rc == 0:
            self._state = ConnectionState.CONNECTED
            self._reconnect_count = 0
            logger.info(f"Connected to {self._config.server}")
            
            # Subscribe to command topics
            cmd_topic = f"vwire/{self._device_id}/cmd/#"
            client.subscribe(cmd_topic, qos=1)

            # Publish retained online status so dashboard reflects availability
            status_topic = f"vwire/{self._device_id}/status"
            client.publish(status_topic, '{"status":"online"}', qos=1, retain=True)
            
            # Call user callback
            if self._on_connected_callback:
                try:
                    self._on_connected_callback()
                except Exception as e:
                    logger.error(f"Error in connected callback: {e}")
        else:
            self._state = ConnectionState.DISCONNECTED
            error_messages = {
                1: "Incorrect protocol version",
                2: "Invalid client identifier", 
                3: "Server unavailable",
                4: "Bad username or password",
                5: "Not authorized",
            }
            logger.error(f"Connection failed: {error_messages.get(rc, f'Unknown ({rc})')}")
    
    def _on_disconnect(self, client, userdata, rc):
        """Handle MQTT disconnection."""
        if self._state == ConnectionState.DISCONNECTED:
            return
        
        self._state = ConnectionState.DISCONNECTED
        
        if rc != 0:
            reason = mqtt.error_string(rc)
            now = time.time()
            # Count rapid disconnects to surface likely token collision (server kicks old client)
            if now - self._last_disconnect_time <= 10:
                self._disconnects_in_window += 1
            else:
                self._disconnects_in_window = 1
            self._last_disconnect_time = now

            msg = f"Unexpected disconnection: {reason} (code: {rc})"
            if self._disconnects_in_window >= 2:
                msg += " | Hint: Broker enforces one active connection per device token. If another device (e.g., Arduino) uses the same token, it will kick this client. Create a separate device/token for each client."
            logger.warning(msg)
        
        if self._on_disconnected_callback:
            try:
                self._on_disconnected_callback()
            except Exception as e:
                logger.error(f"Error in disconnected callback: {e}")
    
    def _on_message(self, client, userdata, msg):
        """Handle incoming MQTT messages."""
        try:
            parts = msg.topic.split("/")
            
            if len(parts) >= 4 and parts[2] == "cmd":
                pin_str = parts[3]
                value = msg.payload.decode("utf-8")
                
                if pin_str.isdigit():
                    pin_str = f"V{pin_str}"
                
                if pin_str.upper().startswith("V"):
                    pin = int(pin_str[1:])
                    self._pin_values[f"V{pin}"] = value
                    
                    handler = self._handlers[self.VIRTUAL_RECEIVE].get(pin)
                    if handler:
                        try:
                            handler(value)
                        except Exception as e:
                            logger.error(f"Error in handler for V{pin}: {e}")
                            
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    # ========== Context Manager ==========
    
    def __enter__(self) -> "Vwire":
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()
