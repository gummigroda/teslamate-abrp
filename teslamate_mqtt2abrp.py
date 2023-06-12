#!/usr/bin/python3

## [ CLI with docopt ]
"""TeslaMate MQTT to ABRP

Usage: 
    teslamate_mqtt2abrp.py [options]

Options:
    --help                          Show this screen.
    -a, --usertoken USERTOKEN       ABRP user token
    -h, --mqttserver MQTTSERVER     MQTT host to connect to.
    -P, --mqttport MQTTPORT         MQTT server port.
    -u, --username USERNAME         MQTT username.
    -p, --password PASSWORD         MQTT password.
    -t, --basetopic BASETOPIC       Topic for messages and status, defaults to 'teslamate-abrp'.
    -c, --carnumber CARNUMBER       Car number from TeslaMate (usually 1).
    -m, --carmodel CARMODEL         Car model according to https://api.iternio.com/1/tlm/get_CARMODELs_list
    -s --tls                        Use TLS connecting to MQTT server, environment variable: MQTT_TLS
    -x --skiplocation               Don't send LAT and LON to ABRP, environment variable: SKIP_LATLON

Note:
    All arguments can also be passed as corresponding OS environment variables using the capitalized variable with the prefix of ABRP_.
"""

## [ IMPORTS ]
import sys
import datetime
import calendar
import os
import paho.mqtt.client as mqtt
import requests
from time import sleep
from docopt import docopt

# Needed to initialize docopt (for CLI)
if __name__ == '__main__':
    arguments = docopt(__doc__)
    #print(arguments)

## [ CONFIGURATION ]
APIKEY = "d49234a5-2553-454e-8321-3fe30b49aa64"

# FUNCTIONS
def niceNow():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def fileSecret(file):
    if os.path.isfile(file):
        fo = open(file,"r")
        sec = fo.read().splitlines()[0]
        if len(sec) > 0:
            return sec
        else:
            return None
    else:
        return None

def getSetting(arguments, name, default=None):
    # Make stuff
    cmd = "--{}".format(name).lower()
    evmnt = "ABRP_{}".format(name).upper()
    dsecret = fileSecret("/run/secrets/{}".format(evmnt))
    print("Getting setting: {}".format(name))
    
    # Command line
    if arguments[cmd] is not None and arguments[cmd] is not False:
        agvalue = (arguments[cmd], "CMD")    
    # Environment
    elif evmnt in os.environ:
        agvalue = (os.environ[evmnt], "ENV")
    # Docker secret
    elif dsecret is not None:
        agvalue = (dsecret, "DOCKER")
    else:
        # Defaults
        agvalue = (default, "DEFAULT")

    # Printouts, nothing important
    if dsecret is None:
        print("\t{} set to: {} from: {}".format(name, agvalue[0],agvalue[1]))
    else:
        print("\t{} set to: '*****' from: {}".format(name, agvalue[1]))
    
    return agvalue[0]

print("Parsing settings...")
MQTTSERVER = getSetting(arguments, "MQTTSERVER")
if MQTTSERVER is None:
    sys.exit("MQTT server address not supplied. Please supply through ENV variables or CLI argument.")

ABRPUSERTOKEN = getSetting(arguments, "USERTOKEN")
if ABRPUSERTOKEN is None:
    sys.exit("User token not supplied. Please generate it through ABRP and supply through ENV variables or CLI argument.")

MQTTUSERNAME = getSetting(arguments, "USERNAME")
MQTTPASSWORD = getSetting(arguments, "PASSWORD")
BASETOPIC = getSetting(arguments, "BASETOPIC","teslamate-abrp")
MQTTPORT = int(getSetting(arguments, "MQTTPORT", 1883))
USETLS = getSetting(arguments, "TLS", False)
SKIPLOCATION = getSetting(arguments, "SKIPLOCATION", False)
CARNUMBER = getSetting(arguments, "CARNUMBER", 1)
CARMODEL = getSetting(arguments, "CARMODEL")

## [ VARS ]
state_topic = BASETOPIC + "/_script_status"
state = "" #car state
prev_state = "" #car state previous loop for tracking
charger_phases = 1
data = { #dictionary of values sent to ABRP API
    "utc": 0,
    "soc": 0,
    "power": 0,
    "speed": 0,
    "lat": 0,
    "lon": 0,
    "elevation": 0,
    "is_charging": False,
    "is_dcfc": False,
    "is_parked": False,
    "est_battery_range": 0,
    "ideal_battery_range": 0,
    "ext_temp": 0,
    "model": "",
    "trim_badging": "",
    "car_model":f"{CARMODEL}",
    "model": "",
    "trim_badging": "",
    "tlm_type": "api",
    "kwh_charged": 0,
    "heading": 0
}

## [ MQTT ]
# Initialize MQTT client and connect
client = mqtt.Client(f"teslamateToABRP-{CARNUMBER}")
if MQTTUSERNAME is not None:
    if MQTTPASSWORD is not None:
        print("Using MQTT username: {} and password '******'".format(MQTTUSERNAME))
        client.username_pw_set(MQTTUSERNAME, MQTTPASSWORD)
    else:
        print("Using MQTT username: {}".format(MQTTUSERNAME))
        client.username_pw_set(MQTTUSERNAME)
else:
    print("Connecting without any username or password")

# Last will
client.will_set(state_topic, payload="offline", qos=2, retain=True)
if USETLS:
    print("Using TLS with MQTT")
    client.tls_set()
print("Trying to connect to {}:{}".format(MQTTSERVER,MQTTPORT))
client.connect(MQTTSERVER, MQTTPORT)

def on_connect(client, userdata, flags, rc):  # The callback for when the client connects to the broker
    # MQTT Error handling
    print("Connection returned result: {} CODE {}".format(mqtt.connack_string(rc),rc))
    if rc != 0:
        sys.exit("Could not connect")
        #client.loop_stop(force=True)
        #client.disconnect()
        #raise SystemExit(1) # This does not force an exit...
    client.publish(state_topic, payload="online", qos=2, retain=True)
    client.subscribe(f"teslamate/cars/{CARNUMBER}/#")

# Process MQTT messages
def on_message(client, userdata, message):
    global data
    global state
    #global charger_phases
    try:
        #extracts message data from the received message
        payload = str(message.payload.decode("utf-8"))


        match (message.topic.split('/')[-1]):

            case "model":
                data["model"] = payload
                if CARMODEL is None: findCarModel()
            case  "trim_badging":
                data["trim_badging"] = payload
            case  "latitude":
                data["lat"] = float(payload)
            case  "longitude":
                data["lon"] = float(payload)
            case  "elevation":
                data["elevation"] = int(payload)
            case  "speed":
                data["speed"] = int(payload)
            case  "power":
                data["power"] = float(payload)
                if(data["is_charging"]==True and int(payload) < -22):
                    data["is_dcfc"] = True
            case  "charger_power":
                if(payload!='' and int(payload)!=0):
                    data["is_charging"] = True
                    if int(payload) > 22:
                        data["is_dcfc"] = True
            case  "heading":
                data["heading"] = int(payload)
            case  "outside_temp":
                data["ext_temp"] = float(payload)
            case  "odometer":
                data["odometer"] = float(payload)
            case  "ideal_battery_range_km":
                data["ideal_battery_range"] = float(payload)
            case  "est_battery_range_km":
                data["est_battery_range"] = float(payload)
            case  "shift_state":
                if payload == "P":
                    data["is_parked"] = True
                elif payload in ["D","R","P"]:
                    data["is_parked"] = False
            case  "state":
                state = payload
                if payload=="driving":
                    data["is_parked"] = False
                    data["is_charging"] = False
                    data["is_dcfc"] = False
                elif payload=="charging":
                    data["is_parked"] = True
                    data["is_charging"] = True
                    data["is_dcfc"] = False
                elif payload=="supercharging":
                    data["is_parked"] = True
                    data["is_charging"] = True
                    data["is_dcfc"] = True
                elif payload in ["online", "suspended", "asleep"]:
                    data["is_parked"] = True
                    data["is_charging"] = False
                    data["is_dcfc"] = False
            case  "usable_battery_level": #State of Charge of the vehicle (what's displayed on the dashboard of the vehicle is preferred)
                data["soc"] = int(payload)
            case  "charge_energy_added":
                data["kwh_charged"] = float(payload)
            case  "charger_phases":
                charger_phases = 3 if int(payload) > 1 else 1
            
            case _:
                # Unhandled
                pass
            
            # Calculate accurate power on AC charging
            #if data["power"] != 0.0 and data["is_charging"] == True and "voltage" in data and "current" in data:
            #    data["power"] = float(data["current"] * data["voltage"] * charger_phases) / 1000.0 * -1

    except:
        print("unexpected exception while processing message:", sys.exc_info()[0], message.topic, message.payload)

# Starts the MQTT loop processing messages
client.on_message = on_message
client.on_connect = on_connect  # Define callback function for successful connection
client.loop_start()

## [ CAR MODEL ]
# Function to find out car model from TeslaMate data
def findCarModel():
    global data
    print("Trying to determine car model from TeslaMate data...")
    
    # Handle model 3 cases
    if data["model"] == "3":
        match data["trim_badging"]:
            case "50":
                data["car_model"] = "3standard"
            case "62":
                data["car_model"] = "3mid"
            case "74":
                data["car_model"] = "3long"
            case "74D":
                data["car_model"] = "3long_awd"
            case "P74D":
                data["car_model"] = "3p20"
            case _:
                print("Your Model 3 trim could not be automatically determined. Trim reported as: "+data["trim_badging"])
                return
    
    # Handle model Y cases
    if data["model"] == "Y":
        match data["trim_badging"]:
            case "74D":
                data["car_model"] = "tesla:my:19:bt37:awd"
            case "P74D":
                data["car_model"] = "tesla:my:19:bt37:perf"
            case "50":
                data["car_model"] = "tesla:my:22:my_lfp:rwd"
            case _:
                print("Your Model Y trim could not be automatically determined. Trim reported as: "+data["trim_badging"])
                return

    # Handle simple cases (aka Model S and Model X)
    else: 
        data["car_model"] = data["model"].lower()+""+data["trim_badging"].lower()

    # Log the determined car model to the console
    if data["car_model"] is not None: 
        print("Car model automatically determined as: "+data["car_model"])
    else: 
        print("Car model could not be automatically determined, please set it through the CLI or environment var according to the documentation for best results.")

## [ ABRP ]
# Function to send data to ABRP
def updateABRP(apiKey, userToken, dataObject):
    try:
        if not "car_model" in dataObject:
            print("Car model not found yet, waiting...")
            return
        if SKIPLOCATION:
            dataObject["lat"] = 0
            dataObject["lon"] = 0
        # Rinse
        del dataObject["_script_info"]
        # Send
        headers = {"Authorization": f"APIKEY {apiKey}"}
        body = {"tlm": dataObject}
        response = requests.post(f"https://api.iternio.com/1/tlm/send?token={userToken}", headers=headers, json=body)
        resp = response.json()
        publish_to_mqtt({"_script_abrp_post_status": resp["status"]})
        if resp["status"] != "ok":
            print("Response from ABRP:", response.text)
            
    except Exception as ex:
        print("Unexpected exception while calling ABRP API:", sys.exc_info()[0])
        print(ex)
        publish_to_mqtt(
            {
                "_script_error_tst": niceNow(),
                "_script_error": ex
            }
        )

def publish_to_mqtt(dataObject):
    dataObject["_script_last_run"] = niceNow()
    for key, value in dataObject.items():
        client.publish(
            "{}/{}".format(BASETOPIC, key),
            payload=value,
            qos=1,
            retain=True
        )    

## [ MAIN ]
# Starts the forever loop updating ABRP
i = -1
while True:
    i+=1
    sleep(1) #refresh rate of 1 cycle per second
    if state != prev_state:
        i = 30
    current_datetime = datetime.datetime.utcnow()
    data["utc"] = calendar.timegm(current_datetime.utctimetuple()) #utc timestamp must be in every message
    
    if state in ["parked", "online", "suspended", "asleep", "offline"]: #if parked, update every 30 cycles/seconds
        if "kwh_charged" in data:
            del data["kwh_charged"]
        if(i%30==0 or i>30):
            data["_script_info"] = "Car is {}, updating every 30s.".format(state)
            updateABRP(APIKEY, ABRPUSERTOKEN, data)
            publish_to_mqtt(data)
            i = 0
    elif state == "charging": #if charging, update every 6 cycles/seconds
        if i%6==0:
            data["_script_info"] = "Car is {}, updating every 6s.".format(state)
            updateABRP(APIKEY, ABRPUSERTOKEN, data)
            publish_to_mqtt(data)
    elif state == "driving": #if driving, update every cycle/second
        data["_script_info"] = "Car is {}, updating every second.".format(state)
        updateABRP(APIKEY, ABRPUSERTOKEN, data)
        publish_to_mqtt(data)
    else:
        print("{} (unknown state), not sending any update to ABRP.".format(datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
        sleep(10)

    # Save state
    prev_state = state
