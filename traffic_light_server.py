import RPi.GPIO as GPIO
from gpiozero import LED
from time import sleep, time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import json
import random
from socketserver import ThreadingMixIn
import sys
import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# --- Global State & Threading Resources ---
state_lock = threading.Lock()
target_mode = "auto"
target_manual_color = "off"
current_mode = "auto"
current_color = "unknown"
last_state_change_time = 0
s_bahn_minutes_away = -1 # -1 means unknown/no train
weather_status = {}      # To hold weather data, e.g., {'temp': 20, 'condition': 'Clear'}

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

# --- S-Bahn Data Fetching Logic ---
def get_next_train_minutes(eva_number, client_id, client_secret):
    PLAN_API_URL = "https://apis.deutschebahn.com/db-api-marketplace/apis/timetables/v1/plan"
    OUTBOUND_DESTINATIONS = ["Kreuzstraße", "Aying", "Höhenkirchen-Siegertsbrunn", "Dürrnhaar", "Hohenbrunn", "Wächterhof"]
    headers = {"DB-Client-Id": client_id, "DB-Api-Key": client_secret, "accept": "application/xml"}
    now = datetime.now()
    all_stops = []
    for i in range(2):
        check_time = now + timedelta(hours=i)
        date, hour = check_time.strftime('%y%m%d'), check_time.strftime('%H')
        try:
            response = requests.get(f"{PLAN_API_URL}/{eva_number}/{date}/{hour}", headers=headers, timeout=15)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            all_stops.extend(root.findall('s'))
        except requests.exceptions.RequestException as e:
            print(f"Error fetching S-Bahn data: {e}", file=sys.stderr)
            return None
    upcoming_departures_minutes = []
    for stop in all_stops:
        try:
            path_string = stop.find('.//dp').get('ppth')
            destination = path_string.split('|')[-1]
            if destination in OUTBOUND_DESTINATIONS: continue
            departure_time_raw = stop.find('.//dp').get('pt')
            departure_dt = datetime.strptime(departure_time_raw, '%y%m%d%H%M')
            if departure_dt < now: continue
            minutes_until = int((departure_dt - now).total_seconds() / 60)
            upcoming_departures_minutes.append(minutes_until)
        except (AttributeError, IndexError): continue
    return min(upcoming_departures_minutes) if upcoming_departures_minutes else None

def s_bahn_monitor():
    """Runs in a separate thread to periodically fetch S-Bahn data."""
    global s_bahn_minutes_away
    client_id, client_secret = os.getenv("DB_CLIENT_ID"), os.getenv("DB_CLIENT_SECRET")
    ottobrunn_eva = "8004733"
    if not client_id or not client_secret:
        print("S-Bahn Monitor disabled: DB API keys not set.", file=sys.stderr)
        return
    while True:
        minutes = get_next_train_minutes(ottobrunn_eva, client_id, client_secret)
        with state_lock:
            s_bahn_minutes_away = minutes if minutes is not None else -1
        sleep(30)

# --- NEW: Biergarten Weather Monitor ---
def weather_monitor():
    """Runs in a separate thread to fetch weather data every 15 minutes."""
    global weather_status
    api_key = os.getenv("OWM_API_KEY")
    # Coordinates for Hohenbrunn, Bavaria
    lat, lon = "48.0667", "11.7167"
    WEATHER_API_URL = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={api_key}&units=metric"
    
    if not api_key:
        print("Biergarten Monitor disabled: OWM_API_KEY not set.", file=sys.stderr)
        return

    while True:
        print("Biergarten Monitor: Fetching weather data...")
        try:
            response = requests.get(WEATHER_API_URL, timeout=15)
            response.raise_for_status()
            data = response.json()
            temp = data.get('main', {}).get('temp')
            condition = data.get('weather', [{}])[0].get('main')
            with state_lock:
                weather_status = {'temp': temp, 'condition': condition}
                print(f"Biergarten Monitor: Weather is {temp}°C, {condition}.")
        except Exception as e:
            print(f"Error fetching weather data: {e}", file=sys.stderr)
            with state_lock:
                weather_status = {} # Clear status on error
        # Fetch weather every 15 minutes
        sleep(900)

# --- Main Controller Thread ---
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
            
            if current_mode != target_mode:
                current_mode = target_mode
                last_state_change_time = now
                if current_mode == 'auto': set_light_state('red'); next_auto_state = 'red_and_yellow'
                elif current_mode == 'sos': sos_index = 0; set_light_state('off')
                elif current_mode == 'idle': set_light_state('off')
            
            if current_mode == 'manual':
                set_light_state(target_manual_color)
            
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
            elif current_mode == "s_bahn":
                minutes = s_bahn_minutes_away
                if minutes == -1: loop_sleep = 0.5; set_light_state('red' if current_color != 'red' else 'off')
                elif minutes < 9: set_light_state('red')
                elif minutes == 9: loop_sleep = 0.5; set_light_state('yellow' if current_color != 'yellow' else 'off')
                elif minutes <= 12: set_light_state('yellow')
                else: set_light_state('green')
            elif current_mode == "biergarten":
                temp = weather_status.get('temp')
                condition = weather_status.get('condition')
                hour = datetime.now().hour
                if temp is None or condition is None:
                    loop_sleep = 0.5; set_light_state('red' if current_color != 'red' else 'off') # No data
                elif hour < 16 or temp < 15 or "Rain" in condition or "Snow" in condition:
                    set_light_state('red') # Forget it
                elif temp < 18 or "Clouds" in condition:
                    set_light_state('yellow') # A bit of a risk
                else: # Temp > 18 and clear
                    set_light_state('green') # Let's go!

        sleep(loop_sleep)

# --- Web Server Class ---
class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global target_mode, target_manual_color
        parsed_path = urlparse(self.path)

        if parsed_path.path == '/status':
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
            with state_lock:
                status = {'color': current_color, 'mode': current_mode, 's_bahn_minutes': s_bahn_minutes_away, 'weather': weather_status}
            self.wfile.write(json.dumps(status).encode('utf-8'))
            return

        query_params = parse_qs(parsed_path.query)
        action = query_params.get('action', [None])[0]
        if action:
            with state_lock:
                if action == 'set_color':
                    clicked_color = query_params['color'][0]
                    if current_mode == 'manual' and current_color == clicked_color:
                        target_manual_color = 'off'
                    else:
                        target_manual_color = clicked_color
                    target_mode = 'manual'
                elif action == 'set_mode':
                    new_mode = query_params['mode'][0]
                    target_mode = 'idle' if current_mode == new_mode else new_mode
            self.send_response(200); self.end_headers()
            return
            
        if parsed_path.path == '/':
            self.send_response(200); self.send_header('Content-type', 'text/html'); self.end_headers()
            html = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <title>Traffic Light Control</title>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <meta name="apple-mobile-web-app-capable" content="yes">
                <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
                <link rel="preconnect" href="https://fonts.googleapis.com">
                <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
                <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
                <style>
                    :root {{
                        --bg-color: #1a1d23;
                        --body-bg: #111317;
                        --text-color: #e0e0e0;
                        --text-muted: #888;
                        --accent-color: #007bff;
                        --shadow-color: rgba(0, 0, 0, 0.5);
                    }}
                    html, body {{
                        height: 100%;
                        margin: 0;
                        padding: 0;
                        background-color: var(--body-bg);
                        font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                        color: var(--text-color);
                        -webkit-tap-highlight-color: transparent;
                        display: flex;
                        justify-content: center;
                        align-items: center;
                    }}
                    .container {{
                        width: 100%;
                        max-width: 380px;
                        padding: 20px;
                        box-sizing: border-box;
                        display: flex;
                        flex-direction: column;
                        align-items: center;
                        gap: 25px;
                    }}
                    .traffic-light-body {{
                        background-color: var(--bg-color);
                        border-radius: 24px;
                        padding: 20px;
                        display: flex;
                        flex-direction: column;
                        gap: 15px;
                        border: 1px solid #333;
                        box-shadow: 0 10px 30px var(--shadow-color);
                    }}
                    .light {{
                        width: 90px;
                        height: 90px;
                        border-radius: 50%;
                        background-color: #333;
                        opacity: 0.5;
                        transition: all 0.15s ease-in-out;
                        cursor: pointer;
                        box-shadow: inset 0 2px 10px rgba(0,0,0,0.4);
                    }}
                    .red-on {{ background-color: #ff1c1c; opacity: 1; box-shadow: 0 0 40px #ff1c1c, inset 0 2px 10px rgba(0,0,0,0.4); }}
                    .yellow-on {{ background-color: #ffc700; opacity: 1; box-shadow: 0 0 40px #ffc700, inset 0 2px 10px rgba(0,0,0,0.4); }}
                    .green-on {{ background-color: #00ff00; opacity: 1; box-shadow: 0 0 40px #00ff00, inset 0 2px 10px rgba(0,0,0,0.4); }}
                    
                    .controls {{ text-align: center; width: 100%; }}
                    #modeText {{
                        font-size: 1.5em;
                        font-weight: 600;
                        margin-top: 0;
                        margin-bottom: 8px;
                    }}
                    .info-text {{
                        height: 22px;
                        font-size: 1em;
                        font-style: italic;
                        color: var(--text-muted);
                        margin-bottom: 20px;
                    }}
                    .mode-buttons {{
                        display: grid;
                        grid-template-columns: 1fr 1fr 1fr;
                        gap: 10px;
                        width: 100%;
                    }}
                    .mode-buttons a {{
                        background-color: #333;
                        color: var(--text-color);
                        padding: 12px 10px;
                        border-radius: 12px;
                        font-size: 1em;
                        font-weight: 600;
                        text-decoration: none;
                        transition: background-color 0.2s, transform 0.1s;
                    }}
                    .mode-buttons a:active {{
                        transform: scale(0.95);
                    }}
                    .mode-buttons a.active {{
                        background-color: var(--accent-color);
                        color: white;
                    }}
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
                        <div id="info-display" class="info-text"></div>
                        <div class="mode-buttons">
                            <a href="#" id="mode-auto" onclick="handleModeClick('auto')">Auto</a>
                            <a href="#" id="mode-emergency" onclick="handleModeClick('emergency')">Emergency</a>
                            <a href="#" id="mode-sos" onclick="handleModeClick('sos')">SOS</a>
                            <a href="#" id="mode-party" onclick="handleModeClick('party')">Party</a>
                            <a href="#" id="mode-s_bahn" onclick="handleModeClick('s_bahn')">S-Bahn</a>
                            <a href="#" id="mode-biergarten" onclick="handleModeClick('biergarten')">Biergarten</a>
                        </div>
                    </div>
                </div>

                <script>
                    let currentModeFromServer = 'unknown';
                    let localAnimationId = null; 

                    function updateVisuals(color, mode, s_bahn_minutes, weather) {{
                        if (currentModeFromServer !== mode) {{
                            const currentActive = document.querySelector('.mode-buttons a.active');
                            if (currentActive) {{ currentActive.classList.remove('active'); }}
                            if (mode !== 'idle' && mode !== 'manual') {{
                                const newActive = document.getElementById(`mode-${{mode}}`);
                                if (newActive) {{ newActive.classList.add('active'); }}
                            }}
                        }}
                        currentModeFromServer = mode;
                        document.querySelector('#modeText strong').textContent = (mode === 'idle') ? 'OFF' : mode.replace('_', ' ').toUpperCase();
                        
                        const infoDisplay = document.getElementById('info-display');
                        if (mode === 's_bahn') {{
                            infoDisplay.textContent = (s_bahn_minutes === -1) ? 'No S-Bahn data.' : `Next train in ${{s_bahn_minutes}} min.`;
                        }} else if (mode === 'biergarten') {{
                            if (weather && weather.temp && weather.condition) {{
                                infoDisplay.textContent = `${{weather.temp.toFixed(1)}}°C, ${{weather.condition}}`;
                            }} else {{
                                infoDisplay.textContent = 'No weather data.';
                            }}
                        }} else {{
                            infoDisplay.textContent = '';
                        }}

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
                            updateVisuals(colors[Math.floor(Math.random() * colors.length)], 'party', -1, {{}});
                        }}, 80);
                    }}

                    function startSosAnimation() {{
                        stopLocalAnimation();
                        const sosPattern = [
                            {{state: 'all_on', duration: 200}}, {{state: 'off', duration: 200}},{{state: 'all_on', duration: 200}}, {{state: 'off', duration: 200}},{{state: 'all_on', duration: 200}}, {{state: 'off', duration: 400}},
                            {{state: 'all_on', duration: 600}}, {{state: 'off', duration: 200}},{{state: 'all_on', duration: 600}}, {{state: 'off', duration: 200}},{{state: 'all_on', duration: 600}}, {{state: 'off', duration: 400}},
                            {{state: 'all_on', duration: 200}}, {{state: 'off', duration: 200}},{{state: 'all_on', 'duration': 200}}, {{state: 'off', duration: 200}},{{state: 'all_on', duration: 200}}, {{state: 'off', duration: 1500}},
                        ];
                        let sosIndex = 0;
                        function runSosStep() {{
                            if (currentModeFromServer !== 'sos') return;
                            const step = sosPattern[sosIndex];
                            updateVisuals(step.state, 'sos', -1, {{}});
                            sosIndex = (sosIndex + 1) % sosPattern.length;
                            localAnimationId = setTimeout(runSosStep, step.duration);
                        }}
                        runSosStep();
                    }}

                    function handleLightClick(color) {{
                        stopLocalAnimation();
                        fetch(`/?action=set_color&color=${{color}}`);
                    }}

                    function handleModeClick(mode) {{
                        const isTogglingOff = currentModeFromServer === mode;
                        stopLocalAnimation(); 
                        fetch(`/?action=set_mode&mode=${{mode}}`);
                        if (!isTogglingOff) {{
                            if (mode === 'party') startPartyAnimation();
                            else if (mode === 'sos') startSosAnimation();
                        }}
                    }}

                    async function syncWithServer() {{
                        if (localAnimationId) return;
                        try {{
                            const response = await fetch('/status');
                            const status = await response.json();
                            updateVisuals(status.color, status.mode, status.s_bahn_minutes, status.weather);
                        }} catch (e) {{}}
                    }}
                    
                    setInterval(syncWithServer, 400);
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
        
        # Start all background threads
        threading.Thread(target=traffic_light_controller, daemon=True).start()
        threading.Thread(target=s_bahn_monitor, daemon=True).start()
        threading.Thread(target=weather_monitor, daemon=True).start()
        
        run_server()
    except KeyboardInterrupt:
        print("\nStopping program.")
        GPIO.cleanup()
