# pi-traffic-light-web

README.md

Raspberry Pi Web-Controlled Traffic Light 🚦

This project turns a Raspberry Pi with three LEDs into a feature-rich, web-controlled traffic light. You can control the lights manually, run realistic traffic patterns, or enable several "crazy" modes, all from a responsive web interface on your phone or computer.

Features

    Real-time Web UI: A mobile-friendly interface to control the lights and switch modes without reloading the page.

    Manual Control: Click any light on the web page to turn the physical LED on or off instantly.

    Multiple Operating Modes:

        Auto (German): Simulates the Red -> Red+Yellow -> Green -> Yellow traffic sequence.

        Emergency: Flashes the yellow light, indicating a signal fault.

        SOS: Flashes the universal distress signal with all three lights.

        Party: Rapidly flashes random lights.

        CPU Monitor: Uses the light color to display the Raspberry Pi's current CPU load.

    Robust & Performant: Built with Python's threading to run the web server and light controller simultaneously without conflicts. The non-blocking design ensures the UI is always responsive.

Hardware

    Raspberry Pi 4 (or any model with GPIO pins)

    3x LEDs (Red, Yellow, Green)

    3x Resistors (e.g., 220Ω - 330Ω)

    Breadboard and jumper wires

Setup & Installation

    Clone the repository:
    Bash

git clone <your-repo-url>
cd pi-traffic-light-web

Connect the Hardware:
Wire the LEDs to the following GPIO pins (using the BCM numbering scheme):

    Red LED: GPIO 17

    Yellow LED: GPIO 27

    Green LED: GPIO 18

Install Dependencies:
Bash

pip install gpiozero psutil

Run the Server:
Bash

    python traffic_light_server.py

    Access the Interface:
    Find your Raspberry Pi's IP address (using hostname -I) and open a web browser on any device on the same network to http://<your_pi_ip>:8000.

