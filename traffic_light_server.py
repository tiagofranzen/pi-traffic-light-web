import RPi.GPIO as GPIO
from gpiozero import LED
from time import sleep, time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import json
import random
import psutil
from socketserver import ThreadingMixIn

# --- Global State & Threading Resources ---
state_lock = threading.Lock()
# "target" variables are set by the web server. The controller works to match them.
target_mode = "auto"
target_manual_color = "off"

# "current" variables represent the actual state of the system.
current_mode = "auto"
current_color = "unknown"
last_state_change_time = 0

# --- GPIO Setup ---
GPIO.setmode(GPIO.BCM)
red = LED(22, active_high=False)
yellow = LED(27, active_high=False)
green = LED(17, active_high=False)
all_lights = [red, yellow, green]

# --- Core Light Control Helper Function ---
def set_light_state(color_to_set):
    """Sets the physical light state. ONLY called by the controller thread."""
    global current_color
    if current_color == color_to_set:
        return
        
    for light in all_lights:
        light.off()

    if color_to_set == "red": red.on()
    elif color_to_set == "yellow": yellow.on()
    elif color_to_set == "green": green.on()
    elif color_to_set == "red_and_yellow": red.on(); yellow.on()
    elif color_to_set == "all_on": red.on(); yellow.on(); green.on()
    
    current_color = color_to_set

# --- Main Controller Thread (Re-architected for Stability) ---
def traffic_light_controller():
    """The single authority for all hardware changes."""
    global target_mode, target_manual_color, current_mode, last_state_change_time
    
    next_auto_state = 'green'
    sos_pattern = [
        {'state': 'all_on', 'duration': 0.2}, {'state': 'off', 'duration': 0.2},{'state': 'all_on', 'duration': 0.2}, {'state': 'off', 'duration': 0.2},{'state': 'all_on', 'duration': 0.2}, {'state': 'off', 'duration': 0.4},
        {'state': 'all_on', 'duration': 0.6}, {'state': 'off', 'duration': 0.2},{'state': 'all_on', 'duration': 0.6}, {'state': 'off', 'duration': 0.2},{'state': 'all_on', 'duration': 0.6}, {'state': 'off', 'duration': 0.4},
        {'state': 'all_on', 'duration': 0.2}, {'state': 'off', 'duration': 0.2},{'state': 'all_on', 'duration': 0.2}, {'state': 'off', 'duration': 0.2},{'state': 'all_on', 'duration': 0.2}, {'state': 'off', 'duration': 1.5},
    ]
    sos_index = 0

    with state_lock:
        set_light_state("green")
        last_state_change_time = time()

    while True:
        loop_sleep = 0.1
        with state_lock:
            now = time()
            
            # Check for and apply commands ("goals") from the web server
            if current_mode != target_mode:
                current_mode = target_mode
                last_state_change_time = now
                if current_mode == 'auto': set_light_state('red'); next_auto_state = 'red_and_yellow'
                elif current_mode == 'sos': sos_index = 0; set_light_state('off')
                elif current_mode == 'idle': set_light_state('off')
            
            if current_mode == 'manual':
                set_light_state(target_manual_color)
            
            # Run the logic for the current active mode
            elapsed = now - last_state_change_time
            if current_mode == "auto":
                if current_color == 'green' and elapsed > 20: set_light_state('yellow'); next_auto_state = 'red'; last_state_change_time = now
                elif current_color == 'yellow' and elapsed > 3: set_light_state(next_auto_state); last_state_change_time = now
                elif current_color == 'red' and elapsed > 20: set_light_state('red_and_yellow'); next_auto_state = 'green'; last_state_change_time = now
                elif current_color == 'red_and_yellow' and elapsed > 2: set_light_state(next_auto_state); last_state_change_time = now
            elif current_mode == "party":
                loop_sleep = 0.08
                set_light_state(random.choice(['red', 'yellow', 'green', 'off']))
            elif current_mode == "emergency":
                loop_sleep = 0.5
                set_light_state('yellow' if current_color != 'yellow' else 'off')
            elif current_mode == "sos":
                current_step = sos_pattern[sos_index]
                if elapsed > current_step['duration']:
                    sos_index = (sos_index + 1) % len(sos_pattern)
                    set_light_state(sos_pattern[sos_index]['state'])
                    last_state_change_time = now
            elif current_mode == "cpu_monitor":
                if elapsed > 2:
                    cpu_percent = psutil.cpu_percent()
                    if cpu_percent < 25: set_light_state('green')
                    elif cpu_percent < 70: set_light_state('yellow')
                    else: set_light_state('red')
                    last_state_change_time = now
        sleep(loop_sleep)

# --- Web Server Class (Simplified to only set targets) ---
class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global target_mode, target_manual_color
        parsed_path = urlparse(self.path)

        if parsed_path.path == '/status':
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
            with state_lock:
                status = {'color': current_color, 'mode': current_mode}
            self.wfile.write(json.dumps(status).encode('utf-8'))
            return

        query_params = parse_qs(parsed_path.query)
        action = query_params.get('action', [None])[0]
        if action:
            with state_lock:
                if action == 'set_color':
                    target_mode = 'manual'
                    target_manual_color = query_params['color'][0]
                elif action == 'set_mode':
                    new_mode = query_params['mode'][0]
                    target_mode = 'idle' if current_mode == new_mode else new_mode
            self.send_response(200); self.end_headers()
            return
            
        if parsed_path.path == '/':
            self.send_response(200); self.send_header('Content-type', 'text/html'); self.end_headers()
            # NOTE: All curly braces for CSS and JS must be escaped by doubling them (e.g., {{ ... }})
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
                    .light {{ width: 80px; height: 80px; border-radius: 50%; background-color: #4d4d4d; opacity: 0.3; transition: all 0.1s ease-in-out; cursor: pointer; }}
                    .red-on {{ background-color: #ff1c1c; opacity: 1; box-shadow: 0 0 35px #ff1c1c; }}
                    .yellow-on {{ background-color: #ffc700; opacity: 1; box-shadow: 0 0 35px #ffc700; }}
                    .green-on {{ background-color: #00ff00; opacity: 1; box-shadow: 0 0 35px #00ff00; }}
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
                    let localAnimationId = null; 

                    function updateVisuals(color, mode) {{
                        if (clientSideMode !== mode) {{
                            const currentActive = document.querySelector('.mode-buttons a.active');
                            if (currentActive) {{ currentActive.classList.remove('active'); }}
                            if (mode !== 'idle' && mode !== 'manual') {{
                                const newActive = document.getElementById(`mode-${{mode}}`);
                                if (newActive) {{ newActive.classList.add('active'); }}
                            }}
                        }}
                        clientSideMode = mode;
                        document.querySelector('#modeText strong').textContent = (mode === 'idle') ? 'OFF' : mode.replace('_', ' ').toUpperCase();
                        
                        const isRedOn = color === 'red' || color === 'red_and_yellow' || color === 'all_on';
                        const isYellowOn = color === 'yellow' || color === 'red_and_yellow' || color === 'all_on';
                        const isGreenOn = color === 'green' || color === 'all_on';
                        document.getElementById('red').className = 'light' + (isRedOn ? ' red-on' : '');
                        document.getElementById('yellow').className = 'light' + (isYellowOn ? ' yellow-on' : '');
                        document.getElementById('green').className = 'light' + (isGreenOn ? ' green-on' : '');
                    }}
                    
                    function stopLocalAnimation() {{
                        if (localAnimationId) {{
                            clearInterval(localAnimationId); 
                            clearTimeout(localAnimationId);
                            localAnimationId = null;
                        }}
                    }}

                    function startPartyAnimation() {{
                        stopLocalAnimation();
                        localAnimationId = setInterval(() => {{
                            const colors = ['red', 'yellow', 'green', 'off'];
                            updateVisuals(colors[Math.floor(Math.random() * colors.length)], 'party');
                        }}, 80);
                    }}

                    function startSosAnimation() {{
                        stopLocalAnimation();
                        const sosPattern = [
                            {{state: 'all_on', duration: 200}}, {{state: 'off', duration: 200}},{{state: 'all_on', duration: 200}}, {{state: 'off', duration: 200}},{{state: 'all_on', duration: 200}}, {{state: 'off', duration: 400}},
                            {{state: 'all_on', duration: 600}}, {{state: 'off', duration: 200}},{{state: 'all_on', duration: 600}}, {{state: 'off', duration: 200}},{{state: 'all_on', duration: 600}}, {{state: 'off', duration: 400}},
                            {{state: 'all_on', duration: 200}}, {{state: 'off', duration: 200}},{{state: 'all_on', duration: 200}}, {{state: 'off', duration: 200}},{{state: 'all_on', duration: 200}}, {{state: 'off', duration: 1500}},
                        ];
                        let sosIndex = 0;
                        function runSosStep() {{
                            if (clientSideMode !== 'sos') return;
                            const step = sosPattern[sosIndex];
                            updateVisuals(step.state, 'sos');
                            sosIndex = (sosIndex + 1) % sosPattern.length;
                            localAnimationId = setTimeout(runSosStep, step.duration);
                        }}
                        runSosStep();
                    }}

                    function handleLightClick(color) {{
                        stopLocalAnimation();
                        const targetColor = (clientSideColor === color && clientSideMode === 'manual') ? 'off' : color;
                        updateVisuals(targetColor, 'manual');
                        fetch(`/?action=set_color&color=${{targetColor}}`);
                    }}

                    function handleModeClick(mode) {{
                        stopLocalAnimation();
                        const isTogglingOff = clientSideMode === mode;
                        const newMode = isTogglingOff ? 'idle' : mode;
                        // Optimistic UI update for mode button
                        updateVisuals(clientSideColor, newMode);
                        fetch(`/?action=set_mode&mode=${{mode}}`).then(() => {{
                            if (newMode === 'party') startPartyAnimation();
                            else if (newMode === 'sos') startSosAnimation();
                        }});
                    }}

                    async function syncWithServer() {{
                        if (localAnimationId) return; // Don't sync if a local animation is running
                        try {{
                            const response = await fetch('/status');
                            const status = await response.json();
                            updateVisuals(status.color, status.mode);
                        }} catch (e) {{
                            // Errors are expected if server restarts, do nothing.
                        }}
                    }}
                    
                    setInterval(syncWithServer, 400); // Poll slightly faster
                    syncWithServer();
                </script>
            </body>
            </html>
            """
            self.wfile.write(html.encode('utf-8'))

# --- Initialization and Server Start ---
def initialization_sequence():
    """Cycles through lights on startup to confirm they work."""
    print("Running initialization sequence...")
    initial_lights = [red, yellow, green];
    for light in initial_lights: light.on(); sleep(0.2); light.off()
    print("Initialization complete.")

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def run_server():
    """Sets up and runs the web server indefinitely."""
    server_address = ('0.0.0.0', 8000)
    httpd = ThreadingHTTPServer(server_address, StatusHandler)
    print(f"Web server running. Access it at http://<your_pi_ip>:8000")
    httpd.serve_forever()

if __name__ == "__main__":
    try:
        initialization_sequence()
        light_thread = threading.Thread(target=traffic_light_controller, daemon=True)
        light_thread.start()
        run_server()
    except KeyboardInterrupt:
        print("\nStopping program.")
        GPIO.cleanup()
