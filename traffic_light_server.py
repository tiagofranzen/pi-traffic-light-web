import RPi.GPIO as GPIO
from gpiozero import LED
from time import sleep, time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import json
import random
import psutil # For CPU Monitor mode

# --- Global State Variables & Threading Lock ---
state_lock = threading.Lock()
current_color = "unknown"
# Mode can now be 'idle'
mode = "auto"
last_state_change_time = 0

# --- GPIO Setup ---
GPIO.setmode(GPIO.BCM)
red = LED(22, active_high=False)
yellow = LED(27, active_high=False)
green = LED(17, active_high=False)
all_lights = [red, yellow, green]

# --- Core Light Control Helper Function ---
def set_light_state(color_to_set):
    """A centralized function to set the light state. Handles all colors and states."""
    global current_color
    
    for light in all_lights:
        light.off()

    if color_to_set == "red":
        red.on()
    elif color_to_set == "yellow":
        yellow.on()
    elif color_to_set == "green":
        green.on()
    elif color_to_set == "red_and_yellow":
        red.on()
        yellow.on()
    elif color_to_set == "all_on":
        red.on()
        yellow.on()
        green.on()
    
    current_color = color_to_set

# --- Main Controller Thread (Updated for 'idle' mode) ---
def traffic_light_controller():
    """This function runs in a separate thread and handles all mode logic."""
    global mode, current_color, last_state_change_time
    
    previous_mode = 'auto'
    next_auto_state = 'green'
    sos_pattern = [
        {'state': 'all_on', 'duration': 0.2}, {'state': 'off', 'duration': 0.2},
        {'state': 'all_on', 'duration': 0.2}, {'state': 'off', 'duration': 0.2},
        {'state': 'all_on', 'duration': 0.2}, {'state': 'off', 'duration': 0.4},
        {'state': 'all_on', 'duration': 0.6}, {'state': 'off', 'duration': 0.2},
        {'state': 'all_on', 'duration': 0.6}, {'state': 'off', 'duration': 0.2},
        {'state': 'all_on', 'duration': 0.6}, {'state': 'off', 'duration': 0.4},
        {'state': 'all_on', 'duration': 0.2}, {'state': 'off', 'duration': 0.2},
        {'state': 'all_on', 'duration': 0.2}, {'state': 'off', 'duration': 0.2},
        {'state': 'all_on', 'duration': 0.2}, {'state': 'off', 'duration': 1.5},
    ]
    sos_index = 0

    with state_lock:
        set_light_state("green")
        last_state_change_time = time()

    while True:
        loop_sleep = 0.1
        
        with state_lock:
            now = time()
            
            if mode != previous_mode:
                last_state_change_time = now
                if mode == 'auto':
                    print("Entering Auto Mode: Resetting to RED.")
                    set_light_state('red')
                    next_auto_state = 'red_and_yellow'
                elif mode == 'sos':
                    sos_index = 0
            previous_mode = mode
            
            elapsed = now - last_state_change_time
            
            # --- Main Mode Dispatcher ---
            if mode == "auto":
                if current_color == 'green' and elapsed > 20:
                    set_light_state('yellow'); next_auto_state = 'red'; last_state_change_time = now
                elif current_color == 'yellow' and elapsed > 3:
                    set_light_state(next_auto_state); last_state_change_time = now
                elif current_color == 'red' and elapsed > 20:
                    set_light_state('red_and_yellow'); next_auto_state = 'green'; last_state_change_time = now
                elif current_color == 'red_and_yellow' and elapsed > 2:
                    set_light_state(next_auto_state); last_state_change_time = now

            elif mode == "party":
                loop_sleep = 0.08
                set_light_state(random.choice(['red', 'yellow', 'green', 'off']))

            elif mode == "emergency":
                loop_sleep = 0.5
                set_light_state('yellow' if current_color != 'yellow' else 'off')
            
            elif mode == "sos":
                current_step = sos_pattern[sos_index]
                if elapsed > current_step['duration']:
                    sos_index = (sos_index + 1) % len(sos_pattern)
                    set_light_state(sos_pattern[sos_index]['state'])
                    last_state_change_time = now
            
            elif mode == "cpu_monitor":
                if elapsed > 2:
                    cpu_percent = psutil.cpu_percent()
                    if cpu_percent < 25: set_light_state('green')
                    elif cpu_percent < 70: set_light_state('yellow')
                    else: set_light_state('red')
                    last_state_change_time = now
            
            elif mode == "idle":
                # Do nothing, system is intentionally off.
                pass
            
        sleep(loop_sleep)

# --- Web Server Class (Updated with toggle logic) ---
class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global mode, current_color, last_state_change_time
        parsed_path = urlparse(self.path)

        if parsed_path.path == '/status':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            with state_lock:
                status = {'color': current_color, 'mode': mode}
            self.wfile.write(json.dumps(status).encode('utf-8'))
            return

        query_params = parse_qs(parsed_path.query)
        action = query_params.get('action', [None])[0]
        if action:
            if action == 'set_color' and 'color' in query_params:
                new_color = query_params['color'][0]
                with state_lock:
                    if mode == "manual" and current_color == new_color:
                        set_light_state("off")
                    elif new_color in ['red', 'yellow', 'green']:
                        mode = "manual"
                        set_light_state(new_color)

            elif action == 'set_mode' and 'mode' in query_params:
                new_mode = query_params['mode'][0]
                with state_lock:
                    # --- CHANGE 1 OF 2: SERVER TOGGLE LOGIC ---
                    # If clicking the currently active mode, switch to 'idle' and turn off lights.
                    if mode == new_mode:
                        mode = "idle"
                        set_light_state("off")
                    # Otherwise, switch to the new mode.
                    else:
                        mode = new_mode
            self.send_response(200)
            self.end_headers()
            return

        if parsed_path.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            html = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <title>Ultimate Traffic Light</title>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <style>
                    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #282c34; display: flex; flex-direction: column; justify-content: center; align-items: center; height: 100vh; margin: 0; color: white; -webkit-tap-highlight-color: transparent; }}
                    .container {{ text-align: center; }}
                    .traffic-light-body {{ background-color: #1a1a1a; border-radius: 20px; padding: 20px; display: inline-flex; flex-direction: column; gap: 15px; border: 5px solid #444; margin-bottom: 20px; }}
                    .light {{ width: 80px; height: 80px; border-radius: 50%; background-color: #4d4d4d; opacity: 0.3; transition: all 0.2s ease-in-out; cursor: pointer; }}
                    .red-on {{ background-color: #ff1c1c; opacity: 1; box-shadow: 0 0 35px #ff1c1c; }}
                    .yellow-on {{ background-color: #ffc700; opacity: 1; box-shadow: 0 0 35px #ffc700; }}
                    .green-on {{ background-color: #00ff00; opacity: 1; box-shadow: 0 0 35px #00ff00; }}
                    .controls h2 {{ margin-bottom: 15px; font-weight: 300; }}
                    .mode-buttons {{ display: flex; flex-wrap: wrap; justify-content: center; gap: 10px; }}
                    .mode-buttons a {{ background-color: #444; color: white; padding: 10px 15px; border-radius: 8px; font-size: 1em; text-decoration: none; transition: background-color 0.2s; }}
                    .mode-buttons a.active {{ background-color: #007bff; font-weight: bold; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="traffic-light-body" id="traffic-light">
                        <div id="red" class="light" onclick="handleLightClick('red')"></div>
                        <div id="yellow" class="light" onclick="handleLightClick('yellow')"></div>
                        <div id="green" class="light" onclick="handleLightClick('green')"></div>
                    </div>
                    <div class="controls">
                        <h2 id="modeText">Current Mode: <strong></strong></h2>
                        <div class="mode-buttons">
                            <a href="#" id="mode-auto" onclick="handleModeClick('auto')">Auto</a>
                            <a href="#" id="mode-emergency" onclick="handleModeClick('emergency')">Emergency</a>
                            <a href="#" id="mode-sos" onclick="handleModeClick('sos')">SOS</a>
                            <a href="#" id="mode-party" onclick="handleModeClick('party')">Party</a>
                            <a href="#" id="mode-cpu_monitor" onclick="handleModeClick('cpu_monitor')">CPU</a>
                        </div>
                    </div>
                </div>

                <script>
                    let clientSideColor = 'unknown';
                    let clientSideMode = 'unknown';

                    function updateVisuals(color, mode) {{
                        // Update active button
                        const currentActive = document.querySelector('.mode-buttons a.active');
                        if (currentActive) currentActive.classList.remove('active');
                        // Only set a new button as active if the mode is not 'idle'
                        if (mode !== 'idle') {{
                            const newActive = document.getElementById(`mode-${{mode}}`);
                            if (newActive) newActive.classList.add('active');
                        }}

                        clientSideColor = color;
                        clientSideMode = mode;
                        
                        document.querySelector('#modeText strong').textContent = mode.replace('_', ' ').toUpperCase();
                        
                        const isRedOn = color === 'red' || color === 'red_and_yellow' || color === 'all_on';
                        const isYellowOn = color === 'yellow' || color === 'red_and_yellow' || color === 'all_on';
                        const isGreenOn = color === 'green' || color === 'all_on';

                        document.getElementById('red').className = 'light' + (isRedOn ? ' red-on' : '');
                        document.getElementById('yellow').className = 'light' + (isYellowOn ? ' yellow-on' : '');
                        document.getElementById('green').className = 'light' + (isGreenOn ? ' green-on' : '');
                    }}
                    
                    function handleLightClick(color) {{
                        if (clientSideMode !== 'manual' && clientSideMode !== 'unknown') {{
                            if (!confirm("This will switch to Manual Mode. Continue?")) return;
                        }}
                        const targetColor = (clientSideColor === color && clientSideMode === 'manual') ? 'off' : color;
                        updateVisuals(targetColor, 'manual');
                        fetch(`/?action=set_color&color=${{color}}`);
                    }}

                    function handleModeClick(mode) {{
                        // --- CHANGE 2 OF 2: JAVASCRIPT TOGGLE LOGIC ---
                        // If clicking the already active mode, turn it off (switch to 'idle').
                        if (clientSideMode === mode) {{
                            updateVisuals('off', 'idle');
                        }} else {{
                            // Otherwise, optimistically switch to the new mode.
                            updateVisuals(clientSideColor, mode);
                        }}
                        fetch(`/?action=set_mode&mode=${{mode}}`);
                    }}

                    async function syncWithServer() {{
                        try {{
                            const response = await fetch('/status');
                            if (!response.ok) return;
                            const status = await response.json();
                            if (clientSideMode !== 'manual' || status.mode !== 'manual') {{
                                updateVisuals(status.color, status.mode);
                            }}
                        }} catch (e) {{
                        }}
                    }}
                    
                    setInterval(syncWithServer, 500);
                    syncWithServer();
                </script>
            </body>
            </html>
            """
            self.wfile.write(html.encode('utf-8'))

# --- New Initialization Function ---
def initialization_sequence():
    """Cycles through lights on startup to confirm they work."""
    print("Running initialization sequence...")
    initial_lights = [red, yellow, green]
    for light in initial_lights:
        light.on()
        sleep(0.2)
        light.off()
    print("Initialization complete.")

# --- Function to Start the Server ---
def run_server():
    """Sets up and runs the web server indefinitely."""
    server_address = ('0.0.0.0', 8000)
    httpd = HTTPServer(server_address, StatusHandler)
    print("Web server running on http://<your_pi_ip>:8000")
    httpd.serve_forever()

# --- Main Execution Block ---
if __name__ == "__main__":
    try:
        initialization_sequence()
        
        light_thread = threading.Thread(target=traffic_light_controller, daemon=True)
        light_thread.start()
        
        run_server()
    except KeyboardInterrupt:
        print("\nStopping program.")
        GPIO.cleanup()
