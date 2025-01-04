import json
import os
import requests
import logging
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request,render_template,jsonify
from twilio.twiml.messaging_response import MessagingResponse
import re
import sqlite3
from sqlite3 import Error
# Load environment variables from .env file
load_dotenv()

# Twilio credentials
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_NUMBER = os.getenv('TWILIO_WHATSAPP_NUMBER')

# Database setup
DB_PATH = 'bookings.db'

# Initialize the database
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            destination TEXT,
            travel_date TEXT,
            train_name TEXT,
            train_number TEXT,
            departure_time TEXT,
            arrival_time TEXT,
            duration TEXT,
            class_details,
            travelers TEXT,
            phone_number TEXT
        )
        """)
        conn.commit()

init_db()

# Session dictionary to store user progress
user_sessions = {}

# Global train_headers for RapidAPI request
train_headers = {
    'x-rapidapi-key': os.getenv("RAPIDAPI_KEY"),  # Fetches the key from the environment variable
    'x-rapidapi-host': "irctc1.p.rapidapi.com"
}
flight_headers = {
    'x-rapidapi-key': os.getenv("RAPIDAPI_KEY"),
    'x-rapidapi-host': "sky-scanner3.p.rapidapi.com"
}

try:
    with open('airportData.json', 'r') as f:
        airport_data = json.load(f)
    with open('trainData.json', 'r') as f:
        station_data  = json.load(f)
except FileNotFoundError as e:
    logger.error(f"Error loading data files: {e}")
    AIRPORT_DATA = {}
    STATION_DATA = {'data': []}

# Function to get station code by name
def get_station_code(station_name):
    for station in station_data['data']:
        if station_name.lower() in station['name'].lower():
            return station['code']
    return None

# Function to fetch trains between stations using RapidAPI
def get_trains_between_stations(from_code, to_code, travel_date):
    url = f"https://irctc1.p.rapidapi.com/api/v3/trainBetweenStations?fromStationCode={from_code}&toStationCode={to_code}&dateOfJourney={travel_date}&resultsPerPage=50"

    try:
        response = requests.get(url, headers=train_headers)
        response.raise_for_status()
        data = response.json()
        return data.get('data', [])  # Return the list of trains or an empty list
    except requests.exceptions.RequestException as e:
        print(f"Error fetching train data: {e}")
        return []

# Function to validate phone number format
def validate_phone_number(phone_number):
    # Assuming the phone number should be in the format +<country_code> <number> (e.g., +1 1234567890)
    pattern = re.compile(r'^\+?[1-9]\d{1,14}$')
    return pattern.match(phone_number)


# Flask app setup
app = Flask(__name__)
# Define a function to retrieve and display booking details in HTML
@app.route("/view_bookings", methods=['GET'])
def view_bookings():
    """Fetch and display all flight bookings in an HTML table"""
    try:
        conn = sqlite3.connect('flight_bookings.db')
        c = conn.cursor()
        
        # Fetch all bookings, passengers, and contact details
        c.execute('''
        SELECT b.booking_id, b.booking_date, b.total_price, 
               b.flight_number, b.departure_city, b.departure_time, 
               b.arrival_city, b.arrival_time, b.duration_minutes, 
               b.stops, b.carrier, b.source_airport_code, 
               b.destination_airport_code, b.travel_date, 
               p.given_names, p.last_name, p.gender, p.date_of_birth, 
               p.nationality, p.passenger_type, 
               c.email, c.phone_number
        FROM bookings b
        LEFT JOIN passengers p ON b.booking_id = p.booking_id
        LEFT JOIN contact_details c ON b.booking_id = c.booking_id
        ''')

        bookings = c.fetchall()
        conn.close()

        # Generate the HTML table with the fetched data
        table_headers = [
            "Booking ID", "Booking Date", "Total Price", "Flight Number", 
            "Departure City", "Departure Time", "Arrival City", "Arrival Time", 
            "Duration (Minutes)", "Stops", "Carrier", "Source Airport Code", 
            "Destination Airport Code", "Travel Date", "Given Names", "Last Name", 
            "Gender", "Date of Birth", "Nationality", "Passenger Type", "Email", "Phone Number"
        ]
        
        # Render HTML table using Flask's render_template (you can include the table in a .html template)
        return render_template("flight.html", bookings=bookings, headers=table_headers)

    except Error as e:
        logging.error(f"Error fetching booking details: {e}")
        return "An error occurred while fetching the booking details."


user_sessions = {}

def create_database():
    """Create SQLite database with enhanced flight details storage"""
    try:
        conn = sqlite3.connect('flight_bookings.db')
        c = conn.cursor()
        
        # Create bookings table with enhanced flight details
        c.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total_price TEXT,
            status TEXT DEFAULT 'pending',
            flight_number TEXT,
            departure_city TEXT,
            departure_time DATETIME,
            arrival_city TEXT,
            arrival_time DATETIME,
            duration_minutes INTEGER,
            stops INTEGER,
            carrier TEXT,
            source_airport_code TEXT,
            destination_airport_code TEXT,
            travel_date DATE
        )
        ''')
        
        # Create passengers table
        c.execute('''
        CREATE TABLE IF NOT EXISTS passengers (
            passenger_id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_id INTEGER,
            given_names TEXT,
            last_name TEXT,
            gender TEXT,
            date_of_birth DATE,
            nationality TEXT,
            passenger_type TEXT,
            FOREIGN KEY (booking_id) REFERENCES bookings (booking_id)
        )
        ''')
        
        # Create contact details table
        c.execute('''
        CREATE TABLE IF NOT EXISTS contact_details (
            contact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_id INTEGER,
            email TEXT,
            phone_number TEXT,
            FOREIGN KEY (booking_id) REFERENCES bookings (booking_id)
        )
        ''')
        
        conn.commit()
        return conn
    except Error as e:
        logging.error(f"Database error: {e}")
        return None

def save_booking_details(session, selected_flight_data, conn):
    """Save complete booking details to database"""
    try:
        c = conn.cursor()
        
        # Extract flight details from the selected flight string
        flight_lines = selected_flight_data.split('\n')
        flight_details = {}
        
        for line in flight_lines:
            if '🔢 Fligth Number:' in line:
                flight_details['flight_number'] = line.split(':')[1].strip()
            elif '💰 Price:' in line:
                flight_details['total_price'] = line.split(':')[1].strip()
            elif '⏰ Departure Time:' in line:
                # Parse complex departure/arrival line
                parts = line.split('|')
                dep_part = parts[0].split('🛫')
                arr_part = parts[1].split('🛬')
                
                # Extract departure details
                flight_details['departure_time'] = dep_part[0].split(': ')[1].strip()
                flight_details['departure_city'] = dep_part[1].strip()[1:-1]  # Remove parentheses
                
                # Extract arrival details
                flight_details['arrival_time'] = arr_part[0].split(': ')[1].strip()
                flight_details['arrival_city'] = arr_part[1].strip()[1:-1]  # Remove parentheses
            elif '⏱ Duration:' in line:
                flight_details['duration'] = int(line.split(':')[1].split()[0].strip())
            elif '🛑 Stops:' in line:
                flight_details['stops'] = 0 if 'Direct' in line else int(line.split('(')[0].split(':')[1].strip())
            elif '🛩 Carrier:' in line:
                flight_details['carrier'] = line.split(':')[1].strip()

        # Insert booking record with complete flight details
        c.execute('''
        INSERT INTO bookings (
            total_price, flight_number, departure_city, departure_time, 
            arrival_city, arrival_time, duration_minutes, stops, carrier,
            source_airport_code, destination_airport_code, travel_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            flight_details['total_price'],
            flight_details['flight_number'],
            flight_details['departure_city'],
            flight_details['departure_time'],
            flight_details['arrival_city'],
            flight_details['arrival_time'],
            flight_details['duration'],
            flight_details['stops'],
            flight_details['carrier'],
            session["data"]["source"],
            session["data"]["destination"],
            session["data"]["travel_date"]
        ))
        
        booking_id = c.lastrowid
        
        # Insert passenger details
        for passenger in session["passenger_details"]:
            c.execute('''
            INSERT INTO passengers (
                booking_id, given_names, last_name, gender,
                date_of_birth, nationality, passenger_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                booking_id,
                passenger["given_names"],
                passenger["last_name"],
                passenger["gender"],
                passenger["date_of_birth"],
                passenger["nationality"],
                passenger["passenger_type"]
            ))
        
        # Insert contact details
        c.execute('''
        INSERT INTO contact_details (booking_id, email, phone_number)
        VALUES (?, ?, ?)
        ''', (
            booking_id,
            session["data"]["email"],
            session["data"]["phone_number"]
        ))
        
        conn.commit()
        return booking_id
    except Error as e:
        logging.error(f"Error saving booking details: {e}")
        conn.rollback()
        return None

def initialize_passenger_session():
    """Initialize a new passenger session"""
    return {
        "step": 0,
        "data": {},
        "flight_list": [],
        "current_index": 0,
        "selected_flight": None,
        "current_passenger": 1,
        "passenger_details": [],
        "booking_id": None
    }

# Function to get airport code by city name
def get_airport_code(airport_name):
    for region in airport_data.values():
        for country, airports in region.items():
            for airport in airports:
                if airport_name.lower() in airport['city'].lower():
                    return airport['code']
    return None

# Search for one-way flights
def search_flights_oneway(from_code='AMD', to_code='BOM', travel_date='2025-02-25', currency="INR", cabinclass="economy"):
    logging.debug(f"Searching one-way flights from {from_code} to {to_code} on {travel_date}")
    url = f"https://sky-scanner3.p.rapidapi.com/flights/search-one-way?fromEntityId={from_code}&toEntityId={to_code}&departDate={travel_date}&market=IN&currency={currency}&cabinClass={cabinclass}"
    try:
        response = requests.get(url, headers=flight_headers)
        response.raise_for_status()
        data = response.json()
        return data.get('data', [])
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching one-way flight data: {e}")
        return []

def format_flight_details(flight_list, start_idx, batch_size=5):
    """Format flight details for batch display"""
    end_idx = min(start_idx + batch_size, len(flight_list))
    formatted_flights = ["Here are the flight options for your search:\n"]
    
    for i in range(start_idx, end_idx):
        formatted_flights.append(flight_list[i])
    
    if end_idx < len(flight_list):
        formatted_flights.append("\nType 'more' to see the next batch of flights.")
    else:
        formatted_flights.append("\nEnd of flight list. Type 'restart' to start a new search.")
    
    return "\n\n".join(formatted_flights), end_idx


@app.route("/whatsapp", methods=['POST'])
def whatsapp_reply():
    incoming_msg = request.values.get('Body', '').strip().lower()
    sender = request.values.get('From', '').strip()
    response = MessagingResponse()
    message = response.message()

    # Handle restart command first, before any other processing
    if incoming_msg == 'restart':
        session = user_sessions.get(sender, initialize_passenger_session())
        session["step"] = 1 # Reset to step 0, or possibly skip this step if you want to resume
        user_sessions[sender] = session  # Reset session state
        message.body("Welcome to the Booking Bot! ✈️\n\nPlease choose an option:\n🚆1. Book a Train\n✈️2. Book a Flight")
        return str(response)

    # Get or initialize session
    session = user_sessions.get(sender, initialize_passenger_session())

    # Initial step handling
    if session["step"] == 0:
        message.body("Welcome to the Booking Bot! \n\nPlease choose an option:\n🚆1. Book a Train\n✈️2. Book a Flight.")
        session["step"] = 1
        user_sessions[sender] = session  # Ensure session is saved
        
    elif session["step"] == 1:
        if incoming_msg == "1":
            message.body("You've selected Train Booking.\n\nPlease enter your source station (e.g., New Delhi):")
            session["step"] = 11
            user_sessions[sender] = session  # Save session after update
        elif incoming_msg == "2":
            message.body("You've selected Flight Booking.\n\nPlease enter your departure airport (e.g., JFK):")
            session["step"] = 10
            user_sessions[sender] = session  # Save session after update
        else:
            # Handle invalid choice and keep user in the same step
            message.body("❌ Invalid choice. Please reply with '1' for Train or '2' for Flight.")
            user_sessions[sender] = session  # Ensure session stays in step 1
            return str(response)

    elif session["step"] == 11:  # Get source station
        source_code = get_station_code(incoming_msg)
        if source_code:
            session["data"]["source"] = source_code
            message.body("👍 Got it! Now, please enter your destination station name.")
            session["step"] = 2
        else:
            message.body("❌ Sorry, I couldn't find that station. Please try again.")

    elif session["step"] == 2:  # Get destination station
        destination_code = get_station_code(incoming_msg)
        if destination_code:
            session["data"]["destination"] = destination_code
            message.body("✈️ Got it! Now, What is your travel date? (format: DD-MM-YY):")
            session["step"] = 3
        else:
            message.body("❌ Sorry, I couldn't find that station. Please try again.")
    elif session["step"] == 3:  # Get travel date
        try:
            travel_date = datetime.strptime(incoming_msg, "%d-%m-%y").date()
            session["data"]["date"] = travel_date
            message.body(f"Your details: \n🗣️ Source: {session['data']['source']} \n📍 Destination: {session['data']['destination']} \n📅 Date: {travel_date.strftime('%d-%m-%y')}\n\nPlease reply 'confirm' to proceed or 'restart' to change.")
            session["step"] = 4
        except ValueError:
            message.body("❌ Invalid date format. Please enter the date in the format DD-MM-YY.")
    elif session["step"] == 4:  # Confirm the details
        if incoming_msg.lower() == "confirm":
            # Retrieve session data (source, destination, and date)
            details = session["data"]
            
            # Fetch trains for the given route and date
            trains = get_trains_between_stations(details["source"], details["destination"], details["date"].strftime("%y-%m-%d"))
            
            # Store fetched train details in session
            session["data"]["trains"] = trains
            
            if trains:
                # Prepare the message with train options
                train_list = "🚉 **Trains Found**:\n\n"
                for idx, train in enumerate(trains[:12]):  # Show the first 12 trains
                    train_list += (
                        f"{idx + 1}. 🚆 {train.get('train_name', 'Unknown Train')} ({train.get('train_number', 'N/A')}) "
                        f"({train.get('train_date', 'N/A')})\n"
                        f"   ⏰ Departure: {train.get('from_std', 'N/A')} | Arrival: {train.get('to_std', 'N/A')}\n"
                        f"   ⏱️ Duration: {train.get('duration', 'N/A')}\n\n"
                    )
                train_list += "Reply with the train number (e.g., '1') to select a train or type 'other' to enter a train manually."
                message.body(train_list)
                session["step"] = 5  # Move to step 5 (train selection)
            elif incoming_msg.lower() == "restart":
            # Restart the process if user chooses 'restart'
                message.body("🔄 Alright, let's start over. Please enter your departure station.")
                session["step"] = 1
            else:
                # If no trains found, ask the user to manually input a train
                message.body(
                    "❌ No trains found for the given route and date. Please provide a train number and name manually.\n\n"
                    "Reply with the train name and number like this: 'Train Name, Train Number'."
                )
                session["step"] = 8  # Move to manual input step

        elif incoming_msg.lower() == "restart":
            # Restart the process if user chooses 'restart'
            message.body("🔄 Alright, let's start over. Please enter your departure station.")
            session["step"] = 1  # Go back to step 1 (departure station input)
        
        else:
            # Handle invalid input for confirmation or restart
            message.body("❌ Invalid response. Reply 'confirm' to proceed or 'restart' to start over.")


    elif session["step"] == 5:  # Train selection step
        if "trains" not in session["data"] or not session["data"]["trains"]:
            message.body("❌ Train list not available. Please restart the booking process.")
            session["step"] = 1  # Restart the flow
        elif incoming_msg.lower() == "other":  # User wants to enter a train manually
            message.body("Please provide the train name and number like this: 'Train Name, Train Number'.")
            session["step"] = 8
        else:
            try:
                train_index = int(incoming_msg) - 1
                trains = session["data"]["trains"]

                if 0 <= train_index < len(trains):
                    selected_train = trains[train_index]
                    session["data"].update({
                        "train_name": selected_train.get("train_name", "Unknown Train"),
                        "train_number": selected_train.get("train_number", "N/A"),
                        "departure_time": selected_train.get("from_std", "N/A"),
                        "arrival_time": selected_train.get("to_std", "N/A"),
                        "duration": selected_train.get("duration", "N/A")
                    })
                    message.body(
             f"🎉 You've selected:\n"
            f"🚆 Train: {selected_train.get('train_name', 'Unknown Train')} "
            f"({selected_train.get('train_number', 'N/A')})\n"
            f"⏰ Departure: {selected_train.get('from_std', 'N/A')} | "
            f"Arrival: {selected_train.get('to_std', 'N/A')}\n"
            f"⏱️ Duration: {selected_train.get('duration', 'N/A')}\nWhich class would you like to book?\n1. General\n2. Sleeper\n3. 3AC\n4. 2AC\n5. 1AC\n"
                    )
                    session["step"] = 6  # Move to class details
                else:
                    message.body("❌ Invalid selection. Please reply with a valid train number from the list.")
            except ValueError:
                message.body("❌ Please reply with a valid train number (e.g., '1').")

    elif session["step"] == 8:  # Manual Train Input
        try:
            train_name, train_number = map(str.strip, incoming_msg.split(","))
            session["data"]["train_name"] = train_name
            session["data"]["train_number"] = train_number
            message.body(f"Train details entered:\nTrain: {train_name} ({train_number})\n.Which class would you like to book?\n1. General\n2. Sleeper\n3. 3AC\n4. 2AC\n5. 1AC")

            session["step"] = 6# Move to class details step
        except ValueError:
            message.body("❌ Invalid format. Please enter the train name and number like this: 'Train Name, Train Number'.")
            return str(response)
    elif session["step"] == 6:  # Class details step
        # Strip and convert the input to lowercase
        class_details_input = incoming_msg.strip()

        # Map numbers to class names
        class_mapping = {
            "1": "general",
            "2": "sleeper",
            "3": "3ac",
            "4": "2ac",
            "5": "1ac"
        }

        # Check if the input is a valid class number
        if class_details_input in class_mapping:
            # Map number to class name
            class_details = class_mapping[class_details_input]

            # Update the session with the selected class
            session["data"]["class_details"] = class_details

            # Confirm the class selection and proceed to traveler details
            message.body(f"✅ Class selected: {class_details.capitalize()}.\nNow, please provide traveler details.\nReply with Name, Age, Gender \n Name, Age, Gender  for each traveler.")
            session["step"] = 7  # Move to traveler details step
        else:
            # Handle invalid class selection
            message.body("❌ Invalid class selection. Please choose from:\n1. General\n2. Sleeper\n3. 3AC\n4. 2AC\n5. 1AC.")
            return str(response)  # Stop further processing if the class is invalid

    elif session["step"] == 7:  # Traveler details input
        # Split the input by new lines, assuming each traveler is entered on a separate line
        details = session["data"]
        traveler_details = incoming_msg.strip().split('\n')

        travelers = []
        for traveler in traveler_details:
            try:
                # Each traveler input should be 'Name, Age, Gender'
                name, age, gender = map(str.strip, traveler.split(","))
                travelers.append({"name": name, "age": age, "gender": gender})
            except ValueError:
                message.body("❌ Invalid traveler detail format. Please use 'Name, Age, Gender' format.")
                return str(response)
        session["data"]["travelers"] = travelers
        message.body("Thank you! Now, please provide your phone number (with country code).")
        session["step"] = 9
    elif session["step"] == 9:  # Phone number step
        phone_number = incoming_msg.strip()
        if validate_phone_number(phone_number):
            session["data"]["phone_number"] = phone_number

            # Ensure that all necessary details are present before saving
            if all(key in session["data"] for key in ["train_name", "train_number", "class_details","travelers", "phone_number"]):
                # Add default values for missing fields
                session["data"].setdefault("departure_time", "N/A")
                session["data"].setdefault("arrival_time", "N/A")
                session["data"].setdefault("duration", "N/A")
                
                # Store the booking in the database
                save_booking(session["data"])
                message.body("✔️ Booking is in process! We will send you details soon.")
                session["step"] = 0  # Reset the session after booking
            else:
                message.body("❌ Some details are missing. Please ensure all details are entered correctly.")
        else:
            message.body("❌ Invalid phone number. Please provide a valid phone number with country code (e.g., +911234567890).")

    elif session["step"] == 10:
        from_code = get_airport_code(incoming_msg)
        if from_code:
            session["data"]["source"] = from_code
            message.body("Great! Now, enter your destination airport (e.g., Mumbai):")
            session["step"] = 12
        else:
            message.body(f"Sorry, we couldn't find an airport code for '{incoming_msg}'. Please provide a valid city name.")

    # Step 2: Destination airport
    elif session["step"] == 12:
        to_code = get_airport_code(incoming_msg)
        if to_code:
            session["data"]["destination"] = to_code
            message.body("Enter your travel date (format: DD-MM-YYYY):")
            session["step"] = 13
        else:
            message.body(f"Sorry, we couldn't find an airport code for '{incoming_msg}'. Please provide a valid city name.")

    # Step 3: Travel date
    elif session["step"] == 13:
        try:
            travel_date = datetime.strptime(incoming_msg, "%d-%m-%Y").strftime("%Y-%m-%d")
            session["data"]["travel_date"] = travel_date
            message.body("Enter number of passengers (format: adults,children,infants)\nExample: 2,1,1")
            session["step"] = 14
        except ValueError:
            message.body("Invalid date format. Please use DD-MM-YYYY.")

    # Step 4: Passenger count
    elif session["step"] == 14:
        try:
            adults, children, infants = map(int, incoming_msg.split(','))
            if all(count >= 0 for count in [adults, children, infants]):
                session["data"].update({
                    "adults": adults,
                    "children": children,
                    "infants": infants
                })
                message.body("Please provide your email address to complete the booking:")
                session["step"] = 15
            else:
                message.body("Please enter valid numbers for passengers (adults,children,infants).")
        except ValueError:
            message.body("Invalid format. Please enter numbers separated by commas (e.g., 2,1,1)")

    # Step 5: Email address collection
    elif session["step"] == 15:
        email_regex = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
        if re.match(email_regex, incoming_msg):
            session["data"]["email"] = incoming_msg
           

            flights = search_flights_oneway(
                session["data"]["source"],
                session["data"]["destination"],
                session["data"]["travel_date"]
            )

            itineraries = flights.get('itineraries', [])
            if itineraries:
                flight_list = []
                for idx, flight in enumerate(itineraries):
                    price = flight.get('price', {}).get('formatted', 'N/A')
                    for leg in flight.get('legs', []):
                        origin_city = leg.get('origin', {}).get('city', 'Unknown')
                        flight_number = leg.get('segments', {})[0]['flightNumber']
                        destination_city = leg.get('destination', {}).get('city', 'Unknown')

                        departure_time = leg.get('departure', 'N/A')
                        parsed_time = datetime.strptime(departure_time, "%Y-%m-%dT%H:%M:%S")
                        formatted_time = parsed_time.strftime("%d-%m-%Y at %H:%M")
                        arrival_time = leg.get('arrival', 'N/A')
                        arrival_parsed_time = datetime.strptime(arrival_time, "%Y-%m-%dT%H:%M:%S")
                        arrival_formatted_time = arrival_parsed_time.strftime("%d-%m-%Y at %H:%M")
                        duration = leg.get('durationInMinutes', 'N/A')
                        stop_count = leg.get('stopCount', 0)

                        stops = "Direct" if stop_count == 0 else f"{stop_count} stop(s)"

                        carriers = leg.get('carriers', {}).get('marketing', [])
                        marketing_carrier = carriers[0].get('name', 'Unknown') if carriers else 'Unknown'

                        flight_details = (
                            f"✈️ **Option {idx + 1}**\n"
                            f"🔢 Fligth Number:{flight_number}\n"
                            f"💰 Price: {price}\n"
                            f"⏰ Departure Time: {formatted_time}🛫({origin_city}) | ⏰ Arrival Time: {arrival_formatted_time}🛬({destination_city})\n"
                            f"⏱ Duration: {duration} minutes\n"
                            f"🛑 Stops: {stops}\n"
                            f"🛩 Carrier: {marketing_carrier}\n"
                            "-----------------------------------"
                        )
                        flight_list.append(flight_details)
                
                session["flight_list"] = flight_list
                session["current_index"] = 0
                formatted_response, new_index = format_flight_details(flight_list, 0)
                session["current_index"] = new_index
                message.body(formatted_response + "\n\nPlease select a flight option by entering the number.")
                session["step"] = 16
            else:
                logging.error("No valid flight data available.")
                message.body("No flight data available. Please check your input and try again.")
        else:
            message.body("Invalid email format. Please provide a valid email address.")


    
    # New step for flight selection
    elif session["step"] == 16:
        try:
            selection = int(incoming_msg)
            if 1 <= selection <= len(session["flight_list"]):
                session["selected_flight"] = session["flight_list"][selection - 1]
                message.body(
                    "Please enter passenger details for Adult 1:\n"
                    "Format: Given names, Last name, Gender (M/F), Date of birth (DD-MM-YYYY), Nationality"
                )
                session["step"] = 17
            else:
                message.body("Invalid selection. Please choose a valid flight number.")
        except ValueError:
            message.body("Please enter a valid flight number.")
    
    # Handle passenger details
    elif session["step"] == 17:
        try:
            given_names, last_name, gender, dob, nationality = [x.strip() for x in incoming_msg.split(',')]
            
            # Validate date format
            datetime.strptime(dob, '%d-%m-%Y')
            
            passenger = {
                "given_names": given_names,
                "last_name": last_name,
                "gender": gender.upper(),
                "date_of_birth": dob,
                "nationality": nationality,
                "passenger_type": "adult"
            }
            
            session["passenger_details"].append(passenger)
            
            if session["current_passenger"] < session["data"]["adults"]:
                session["current_passenger"] += 1
                message.body(
                    f"Please enter passenger details for Adult {session['current_passenger']}:\n"
                    "Format: Given names, Last name, Gender (M/F), Date of birth (DD-MM-YYYY), Nationality"
                )
            else:
                message.body(
                    "Please enter contact details:\n"
                    "Format: Phone number (with country code)"
                )
                session["step"] = 18
                
        except (ValueError, IndexError):
            message.body(
                "Invalid format. Please use the format:\n"
                "Given names, Last name, Gender (M/F), Date of birth (DD-MM-YYYY), Nationality"
            )
    
    elif session["step"] == 18:
        phone_number = incoming_msg.strip()
        if phone_number.startswith('+') and phone_number[1:].isdigit():
            session["data"]["phone_number"] = phone_number
            
            # Create database entry
            conn = create_database()
            if conn:
                try:
                    booking_id = save_booking_details(session, session["selected_flight"], conn)
                    
                    if booking_id:
                        # Format confirmation message with full details
                        confirmation_message = (
                            "✅ Booking in process\n\n"
                            f"Booking ID: {booking_id}\n\n"
                            "Flight Details:\n"
                            f"{session['selected_flight']}\n\n"
                            "Passenger Details:\n"
                        )
                        
                        for idx, passenger in enumerate(session["passenger_details"], 1):
                            confirmation_message += (
                                f"Passenger {idx}:\n"
                                f"Name: {passenger['given_names']} {passenger['last_name']}\n"
                                f"Nationality: {passenger['nationality']}\n"
                            )
                        
                        confirmation_message += (
                            "\nContact Details:\n"
                            f"Email: {session['data']['email']}\n"
                            f"Phone: {session['data']['phone_number']}\n\n"
                            "A confirmation email will be sent shortly.\n\n"
                            "Type 'restart' to make a new booking."
                        )
                        
                        message.body(confirmation_message)
                        
                        # Reset session
                        session = initialize_passenger_session()
                    else:
                        message.body("An error occurred while saving your booking. Please try again.")
                except Error as e:
                    logging.error(f"Database error: {e}")
                    message.body("An error occurred while processing your booking. Please try again.")
                finally:
                    conn.close()
            else:
                message.body("An error occurred while processing your booking. Please try again.")
        else:
            message.body("Invalid phone number format. Please include country code (e.g., +1234567890)")

    user_sessions[sender] = session
    return str(response)

# Function to save booking to database
def save_booking(data):

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO bookings (
            source, destination, travel_date, train_name, train_number,
            departure_time, arrival_time, duration,class_details, travelers,phone_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,?)
        """, (
            data["source"], data["destination"], data["date"].strftime("%d-%m-%y"),
            data["train_name"], data["train_number"],
            data.get("departure_time", "N/A"), data.get("arrival_time", "N/A"),
            data.get("duration", "N/A"),
            data.get("class_details","N/A"),
            json.dumps(data["travelers"]),
             data["phone_number"]
        ))
        conn.commit()

# Function to fetch all booking records from the database
def fetch_bookings():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bookings")
        bookings = cursor.fetchall()  # Fetch all records
        
    formatted_bookings = []
    for booking in bookings:
        # Handle JSONDecodeError gracefully
        try:
            travelers = json.loads(booking[10])  # Attempt to parse the travelers field
        except json.JSONDecodeError:
            travelers = []  # If the JSON is invalid, set it as an empty list
        
        formatted_bookings.append({
            "id": booking[0],
            "source": booking[1],
            "destination": booking[2],
            "travel_date": booking[3],
            "train_name": booking[4],
            "train_number": booking[5],
            "departure_time": booking[6],
            "arrival_time": booking[7],
            "duration": booking[8],
            "class_details":booking[9],
            "travelers": travelers,  # Add the parsed travelers data
            "phone_number": booking[11]
        })
    
    return formatted_bookings


# Route to display the details in JSON format
# @app.route('/train_bookings', methods=['GET'])
# def show_bookings_json():
#     bookings = fetch_bookings()
#     # Return bookings as JSON
    # return jsonify(bookings)

# Route to display the details in an HTML table (user-friendly)
@app.route('/train_bookings', methods=['GET'])
def show_bookings_html():
    bookings = fetch_bookings()
    # Pass the formatted bookings to the HTML template
    return render_template("bookings.html", bookings=bookings)



if __name__ == "__main__":
    create_database()
    app.run(debug=True)
