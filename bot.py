import json
import os
import requests
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request, render_template, jsonify, session
from twilio.twiml.messaging_response import MessagingResponse
import re
import sqlite3
from sqlite3 import Error
from flask_session import Session
import redis
from bot import get_airport_code,get_station_code


# Load environment variables from .env file
load_dotenv()

# Twilio credentials
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_NUMBER = os.getenv('TWILIO_WHATSAPP_NUMBER')
# Flask app setup

app = Flask(__name__)
if os.getenv('REDIS_URL'):
    app.config['SESSION_TYPE'] = 'redis'
    app.config['SESSION_REDIS'] = redis.from_url(os.getenv('REDIS_URL'))
else:
    # Fallback to filesystem session
    app.config['SESSION_TYPE'] = 'filesystem'
    app.config['SESSION_FILE_DIR'] = '/tmp/flask_session'

app.config['SESSION_PERMANENT'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = 1800  # 30 minutes
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key-here')
Session(app)

# Global train_headers for RapidAPI request
train_headers = {
    'x-rapidapi-key': os.getenv("RAPIDAPI_KEY"),  # Fetches the key from the environment variable
    'x-rapidapi-host': "irctc1.p.rapidapi.com"
}
flight_headers = {
    'x-rapidapi-key': os.getenv("RAPIDAPI_KEY"),
    'x-rapidapi-host': "sky-scanner3.p.rapidapi.com"
}


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
        
        return "An error occurred while fetching the booking details."




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
        
        conn.rollback()
        return None

def initialize_session():
    """Initialize a new session with default values"""
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




def get_live_status(train_no, day):
    url = f"https://irctc1.p.rapidapi.com/api/v1/liveTrainStatus?trainNo={train_no}&startDay={Day}"
    
    try:
        response = requests.get(url, headers=train_headers)
        response.raise_for_status()
        data = response.json()
        if data.get("status") and data.get("data"):
            return format_live_train_status(data["data"])
        else:
            return "❌ Unable to fetch train status. Please try again."
    except requests.exceptions.RequestException as e:
        return f"❌ Error fetching status details: {e}"




# Search for one-way flights
def search_flights_oneway(from_code, to_code, travel_date, children, infants, adults, currency="INR", cabinclass="economy"):

    
    url = f"https://sky-scanner3.p.rapidapi.com/flights/search-one-way?fromEntityId={from_code}&toEntityId={to_code}&departDate={travel_date}&market=IN&currency={currency}&children={children}&infants={infants}&cabinClass={cabinclass}&adults={adults}"
    try:
        response = requests.get(url, headers=flight_headers)
        response.raise_for_status()
        data = response.json()
        return data.get('data', [])
    except requests.exceptions.RequestException as e:
        return []

def format_train_details(train_list, start_idx, batch_size=5):
    """Format train details for batch display"""
    end_idx = min(start_idx + batch_size, len(train_list))
    formatted_trains = ["Here are the train options for your search:\n"]
    
    for i in range(start_idx, end_idx):
        formatted_trains.append(train_list[i])
    
    if end_idx < len(train_list):
        formatted_trains.append("\nType 'more' to see more trains, or select a train number to proceed.")
    else:
        formatted_trains.append("\nEnd of train list. Select a train number or type 'other' to enter manually.")
    
    return "\n\n".join(formatted_trains), end_idx

def format_flight_details(flight_list, start_idx, batch_size=3):
    """Format flight details for batch display"""
    end_idx = min(start_idx + batch_size, len(flight_list))
    formatted_flights = ["Here are the flight options for your search:\n"]
    
    for i in range(start_idx, end_idx):
        formatted_flights.append(flight_list[i])
    
    if end_idx < len(flight_list):
        formatted_flights.append("\nType 'more' to see more flights, or select a flight number to proceed.")
    else:
        formatted_flights.append("\nEnd of flight list. Please select a flight number to proceed.")
    
    return "\n\n".join(formatted_flights), end_idx


def handle_train_booking(session_data, incoming_msg, message):
    """
    Handle train booking logic based on current step
    Returns: Updated session and message response
    """
    if session_data["step"] == 11:  # Get source station
        source_code = get_station_code(incoming_msg)
        if source_code:
            session_data["data"]["source"] = source_code
            message.body("👍 Got it! Now, please enter your destination station (eg.Vadodara or BRC).")
            session_data["step"] = 2
        else:
            message.body("❌ Sorry, I couldn't find that station. Please try again.")

    elif session_data["step"] == 2:  # Get destination station
        destination_code = get_station_code(incoming_msg)
        if destination_code:
            session_data["data"]["destination"] = destination_code
            message.body("🚉  Got it! Now, What is your travel date? (format: DD-MM-YYYY):")
            session_data["step"] = 3
        else:
            message.body("❌ Sorry, I couldn't find that station. Please try again.")

    elif session_data["step"] == 3:  # Get travel date
        try:
            travel_date = datetime.strptime(incoming_msg, "%d-%m-%Y").date()
            session_data["data"]["date"] = travel_date
            message.body(f"Your details: \n🗣️ Source: {session_data['data']['source']} \n📍 Destination: {session_data['data']['destination']} \n📅 Date: {travel_date.strftime('%d-%m-%Y')}\n\nPlease reply 'confirm' to proceed or 'restart' to change.")
            session_data["step"] = 4
        except ValueError:
            message.body("❌ Invalid date format. Please enter the date in the format DD-MM-YYYY.")

    elif session_data["step"] == 4:  # Confirm the details
        if incoming_msg.lower() == "confirm":
            details = session_data["data"]
            trains = get_trains_between_stations(details["source"], details["destination"], details["date"].strftime("%Y-%m-%d"))
            session_data["data"]["trains"] = trains
            
            if trains:
                # Format the train list as a string
                train_list = []
                for idx, train in enumerate(trains):  # Remove limit of 10 trains
                    train_list.append(
                        f"{idx + 1}. 🚆 {train.get('train_name', 'Unknown Train')} ({train.get('train_number', 'N/A')}) "
                        f"({train.get('train_date', 'N/A')})\n"
                        f"   ⏰ Departure: {train.get('from_std', 'N/A')} | Arrival: {train.get('to_std', 'N/A')}\n"
                        f"   ⏱️ Duration: {train.get('duration', 'N/A')}\n"
                    )

                session_data["train_list"] = train_list
                session_data["current_index"] = 0

                # Get the formatted train details and update current index
                formatted_response, new_index = format_train_details(train_list, 0)
                session_data["current_index"] = new_index

                message.body(formatted_response + "\n\nReply with the train number (e.g., '1') to select a train, 'more' to see more options, or type 'other' to enter a train manually.")
                session_data["step"] = 5
            else:
                message.body(
                    "❌ No trains found for the given route and date. Please provide a train number and name manually.\n\n"
                    "Reply with the train name and number like this: 'Train Name, Train Number'."
                )
                session_data["step"] = 8

    elif session_data["step"] == 5:  # Train selection step
        if incoming_msg.lower() == "more":
            if session_data.get("train_list"):
                formatted_response, new_index = format_train_details(
                    session_data["train_list"],
                    session_data["current_index"]
                )
                session_data["current_index"] = new_index
                message.body(formatted_response + "\n\nReply with the train number to select, 'more' for more options, or 'other' to enter manually.")
            else:
                message.body("No more trains available. Please select from the shown options or type 'other' to enter manually.")
        elif incoming_msg.lower() == "other":
            message.body("Please provide the train name and number like this: 'Train Name, Train Number'.")
            session_data["step"] = 8
        else:
            try:
                train_index = int(incoming_msg) - 1
                trains = session_data["data"]["trains"]

                if 0 <= train_index < len(trains):
                    selected_train = trains[train_index]
                    session_data["data"].update({
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
                        f"⏱️ Duration: {selected_train.get('duration', 'N/A')}\n"
                        f"Which class would you like to book?\n1. General\n2. Sleeper\n3. 3AC\n4. 2AC\n5. 1AC\n"
                    )
                    session_data["step"] = 6  # Move to class details
                else:
                    message.body("❌ Invalid selection. Please reply with a valid train number from the list.")
            except ValueError:
                message.body("❌ Please reply with a valid train number (e.g., '1').")

    elif session_data["step"] == 8:  # Manual Train Input
        try:
            train_name, train_number = map(str.strip, incoming_msg.split(","))
            session_data["data"]["train_name"] = train_name
            session_data["data"]["train_number"] = train_number
            message.body(f"Train details entered:\nTrain: {train_name} ({train_number})\n.Which class would you like to book?\n1. General\n2. Sleeper\n3. 3AC\n4. 2AC\n5. 1AC")

            session_data["step"] = 6# Move to class details step
        except ValueError:
            message.body("❌ Invalid format. Please enter the train name and number like this: 'Train Name, Train Number'.")
            return str(response)
    elif session_data["step"] == 6:  # Class details step
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
            session_data["data"]["class_details"] = class_details

            # Confirm the class selection and proceed to traveler details
            message.body(f"✅ Class selected: {class_details.capitalize()}.\n\nNow, please provide traveler details for each traveler.\n\nReply with Full Name, Age, Gender \n\n if more then one passanger add in next line\n\neg:name,age,gender\n  name,age,gender ")
            session_data["step"] = 7  # Move to traveler details step
        else:
            # Handle invalid class selection
            message.body("❌ Invalid class selection. Please choose from:\n1. General\n2. Sleeper\n3. 3AC\n4. 2AC\n5. 1AC.")
             # Stop further processing if the class is invalid

    elif session_data["step"] == 7:  # Traveler details input
        # Split the input by new lines, assuming each traveler is entered on a separate line
        details = session_data["data"]
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
        session_data["data"]["travelers"] = travelers
        message.body("Thank you! Now, please provide your 📞 phone number (with country code).")
        session_data["step"] = 9
    elif session_data["step"] == 9:  # Phone number step
        phone_number = incoming_msg.strip()
        if validate_phone_number(phone_number):
            session_data["data"]["phone_number"] = phone_number

            # Ensure that all necessary details are present before saving
            if all(key in session_data["data"] for key in ["train_name", "train_number", "class_details","travelers", "phone_number"]):
                # Add default values for missing fields
                session_data["data"].setdefault("departure_time", "N/A")
                session_data["data"].setdefault("arrival_time", "N/A")
                session_data["data"].setdefault("duration", "N/A")
                
                # Store the booking in the database
                save_booking(session_data["data"])
                message.body("✔️ Booking is in process! We will send you details soon.")
                session_data["step"] = 0  # Reset the session after booking
            else:
                message.body("❌ Some details are missing. Please ensure all details are entered correctly.")
        else:
            message.body("❌ Invalid phone number. Please provide a valid phone number with country code (e.g., +911234567890).")


    return session

    return age
def handle_flight_booking(session_data, incoming_msg, message):
    """
    Handle flight booking logic based on current step
    Returns: Updated session and message response
    """
    if session_data["step"] == 10:  # Get departure airport
        from_code = get_airport_code(incoming_msg)
        if from_code:
            session_data["data"]["source"] = from_code
            message.body("🛬Great! Now, enter your destination airport (e.g., Mumbai or BOM):")
            session_data["step"] = 12
        else:
            message.body(f"Sorry, we couldn't find an airport for '{incoming_msg}'. Please provide a valid city name or code.")

    elif session_data["step"] == 12:  # Get destination airport
        to_code = get_airport_code(incoming_msg)
        if to_code:
            session_data["data"]["destination"] = to_code
            message.body("📅Enter your travel date (format: DD-MM-YYYY):")
            session_data["step"] = 13
        else:
            message.body(f"Sorry, we couldn't find an airport  for '{incoming_msg}'. Please provide a valid city name or code.")

    elif session_data["step"] == 13:  # Get travel date
        try:
            travel_date = datetime.strptime(incoming_msg, "%d-%m-%Y").strftime("%Y-%m-%d")
            session_data["data"]["travel_date"] = travel_date
            message.body("Which class would you like to book?\n1. Economy\n2. Premium Economy \n3. Business\n4. First")
            session_data["step"] = 14
        except ValueError:
            message.body("Invalid date format. Please use DD-MM-YYYY.")
    
    elif session_data["step"] == 14:  # Class details step
        # Strip and convert the input to lowercase
        cabin_class = incoming_msg.strip()

        # Map numbers to class names
        class_mapping = {
            "1": "Economy",
            "2": "Premium Economy",
            "3": "Business",
            "4": "First"
          
        }

        # Check if the input is a valid class number
        if cabin_class in class_mapping:
            # Map number to class name
            class_details = class_mapping[cabin_class]

            # Update the session with the selected class
            session_data["data"]["class_details"] = class_details

            # Confirm the class selection and proceed to traveler details
            message.body(f"✅ Class selected: {class_details.capitalize()}.\n\nEnter number of passengers (format: 🧑‍🤝‍🧑adults,🧒children,👶infants).\n\nEach adult can only accompany a maximum of 1 infant.\n\nExample: 2,1,1")
            session_data["step"] = 15  # Move to traveler details step
        else:
            # Handle invalid class selection
            message.body("❌ Invalid class selection. Please choose from:\n1. Economy\n2. Premium Economy \n3. Business\n4. First.")
             # Stop further processing if the class is invalid
        

    elif session_data["step"] == 15:  # Get passenger count
        try:
            adults, children, infants = map(int, incoming_msg.split(','))
            if all(count >= 0 for count in [adults, children, infants]) and infants<=adults:
                session_data["data"].update({
                    "adults": adults,
                    "children": children,
                    "infants": infants
                })
                message.body("📧Please provide your email address to complete the booking:")
                session_data["step"] = 16
            else:
                message.body(f"Please enter valid numbers for passengers (🧑‍🤝‍🧑adults,🧒children,👶infants).\n\n"
                f"Please ensure that the number of 👶 infants is less than or equal to the number of 🧑‍🤝‍🧑 adults, and that all numbers are valid.")
        except ValueError:
            message.body("Invalid format. Please enter numbers separated by commas (e.g., 2,1,1)")
    # Step 5: Email address collection
    elif session_data["step"] == 16:
        email_regex = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
        if re.match(email_regex, incoming_msg):
            session_data["data"]["email"] = incoming_msg
           

            adults = session_data["data"]["adults"]
            children = session_data["data"]["children"]
            infants = session_data["data"]["infants"]
            cabinclass = session_data["data"]["class_details"] 

            # Proceed with flight search using session data and passenger details
            flights = search_flights_oneway(
                session_data["data"]["source"],
                session_data["data"]["destination"],
                session_data["data"]["travel_date"],
                adults=adults,
                children=children,
                infants=infants,
                cabinclass=cabinclass
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
                
                session_data["flight_list"] = flight_list
                session_data["current_index"] = 0
                formatted_response, new_index = format_flight_details(flight_list, 0)
                session_data["current_index"] = new_index
                message.body(formatted_response + "\n\nPlease select a flight option by entering the number  (e.g., '1').")
                session_data["step"] = 17
            else:
                message.body("No flight data available. Please provide your email address to continue, or type 'restart' to start over.")
        else:
            message.body("Invalid email format. Please provide a valid email address.")


    
    elif session_data["step"] == 17:  # Flight selection step
        if incoming_msg.lower() == "more":
            if session_data.get("flight_list"):
                formatted_response, new_index = format_flight_details(
                    session_data["flight_list"],
                    session_data["current_index"]
                )
                session_data["current_index"] = new_index
                message.body(formatted_response + "\n\nPlease select a flight option by entering the number (e.g., '1'), or type 'more' to see more options.")
            else:
                message.body("No more flights available. Please select from the shown options.")
        else:
            try:
                selection = int(incoming_msg)
                if 1 <= selection <= len(session_data["flight_list"]):
                    session_data["selected_flight"] = session_data["flight_list"][selection - 1]
                    message.body(
                        "Please enter passenger details for Adult 1:\n"
                        "details:\n1.Given names\n2 Last name\n3.Gender (M/F)\n4.Date of birth (DD-MM-YYYY)\n5.Nationality"
                        "\n\n🇫 🇴 🇷 🇲 🇦 🇹 eg:name,lastname,gender,date of birth,nationality"
                    )
                    session_data["step"] = 18
                else:
                    message.body("Invalid selection. Please choose a valid flight number.")
            except ValueError:
                message.body("Please enter a valid flight number.")
    
    # Handle passenger details
    elif session_data["step"] == 18:
        try:
            given_names, last_name, gender, dob, nationality = [x.strip() for x in incoming_msg.split(',')]

            
            # Validate date format
            datetime.strptime(dob, '%d-%m-%Y')
                   # Determine passenger type based on current count
            if session_data["current_passenger"] <= session_data["data"]["adults"]:
                passenger_type = "adult"
            elif session_data["current_passenger"] <= (session_data["data"]["adults"] + session_data["data"]["children"]):
                passenger_type = "child"
            else:
                passenger_type = "infant"
            
            passenger = {
                "given_names": given_names,
                "last_name": last_name,
                "gender": gender.upper(),
                "date_of_birth": dob,
                "nationality": nationality,
                "passenger_type": passenger_type
            }
            
            session_data["passenger_details"].append(passenger)
            
            if session_data["current_passenger"] < session_data["data"]["adults"]:
                session_data["current_passenger"] += 1
                message.body(
                    f"Please enter passenger details for 🧑‍🤝‍🧑  Adult {session_data['current_passenger']}:\n"
                    "Format: Given names, Last name, Gender (M/F), Date of birth (DD-MM-YYYY), Nationality"
                )
            elif session_data["current_passenger"] < session_data["data"]["adults"] + session_data["data"]["children"]:
                session_data["current_passenger"] += 1
                message.body(
                    f"Please enter passenger details for 🧒Child {session_data['current_passenger']}:\n"
                    "Format: Given names, Last name, Gender (M/F), Date of birth (DD-MM-YYYY), Nationality"
                )
            elif session_data["current_passenger"] < session_data["data"]["adults"] + session_data["data"]["children"] + session_data["data"]["infants"]:
                session_data["current_passenger"] += 1
                message.body(
                    f"Please enter passenger details for 👶 Infant {session_data['current_passenger']}:\n"
                    "Format: Given names, Last name, Gender (M/F), Date of birth (DD-MM-YYYY), Nationality"
                )
            else:
                message.body(
                    "Please enter contact details:\n"
                    "Format: Phone number (with country code)"
                )
                session_data["step"] = 19
                
        except (ValueError, IndexError):
            message.body(
                "Invalid format. Please use the format:\n"
                "Given names, Last name, Gender (M/F), Date of birth (DD-MM-YYYY), Nationality"
            )
    
    elif session_data["step"] == 19:
        phone_number = incoming_msg.strip()
        if phone_number.startswith('+') and phone_number[1:].isdigit():
            session_data["data"]["phone_number"] = phone_number
            
            # Create database entry
            conn = create_database()
            if conn:
                try:
                    booking_id = save_booking_details(session_data, session_data["selected_flight"], conn)
                    
                    if booking_id:
                        # Format confirmation message with full details
                        confirmation_message = (
                            "✅ Booking in process\n\n"
                            f"Booking ID: {booking_id}\n\n"
                            "Flight Details:\n"
                            f"{session_data['selected_flight']}\n\n"
                            "Passenger Details:\n"
                        )
                        
                        for idx, passenger in enumerate(session_data["passenger_details"], 1):
                            # Assuming line is the string from which you're extracting the Date of Birth (DOB)
                            dob = line.split('📅DOB: ')[1].strip()  # Extract DOB from the line

                            # Assign the extracted DOB to the passenger's dictionary
                            passenger['dob'] = dob
                            confirmation_message += (
                                f"Passenger {idx}:- {passenger['passenger_type']}\n"
                                f"🧑Name: {passenger['given_names']} {passenger['last_name']}\n"
                                f"📅 Date of Birth: {passenger['dob']}\n"
                                f"🧑Gender:{passenger['gender']}\n"
                                f"🌍Nationality: {passenger['nationality']}\n"
                            )
                        
                        confirmation_message += (
                            "\nContact Details:\n"
                            f"Email: {session_data['data']['email']}\n"
                            f"Phone: {session_data['data']['phone_number']}\n\n"
                            "A confirmation email will be sent shortly.\n\n"
                            "Type 'restart' to make a new booking."
                        )
                        
                        message.body(confirmation_message)
                        
                        # Reset session
                        session = initialize_session()
                    else:
                        message.body("An error occurred while saving your booking. Please try again.")
                except Error as e:
                    message.body("An error occurred while processing your booking. Please try again.")
                finally:
                    conn.close()
            else:
                message.body("An error occurred while processing your booking. Please try again.")
        else:
            message.body("Invalid phone number format. Please include country code (e.g., +1234567890)")
    return session_data

def handle_restart(sender, message):
    """
    Handle restart command and initialize a new session
    Returns: New session
    """
    new_session = initialize_session()  # Initialize a new session
    message.body("Type 0 for restart")
    return new_session

def get_session_data():
    """Get or create new session data"""
    if 'session_data' not in session:
        session['session_data'] = initialize_session()  # Create a new session
    return session['session_data']

def save_session_data(data):
    """Save session data"""
    session['session_data'] = data
# pnr 
def format_pnr_details(pnr_data):
    if not pnr_data:
        return "❌ Sorry, couldn't fetch PNR details at the moment."
    
    passenger_details = ""
    for passenger in pnr_data["PassengerStatus"]:
        passenger_details += f"""
👤 Passenger {passenger['Number']}:
   • Status: {passenger['CurrentStatus']} 
   • Coach: {passenger['Coach']}
   • Berth: {passenger['Berth']}
   • Booking Status: {passenger['BookingStatus']}
   """
    
    # Format the coach positions
    coach_positions = pnr_data['CoachPosition'].split()
    formatted_coach_positions = "🚂-" + "-".join(coach_positions)

    # Construct the message with all the details
    message = f"""
🎫 PNR Details ({pnr_data['Pnr']})
━━━━━━━━━━━━━━━━━━━━━━━━
🚂 Train Information
   • Train: {pnr_data['TrainNo']} - {pnr_data['TrainName']}
   • Class: {pnr_data['Class']}
   • Date of Journey: {pnr_data['Doj']}

📍 Station Details
   • From: {pnr_data['SourceName']} ({pnr_data['From']})
   • To: {pnr_data['ReservationUptoName']} ({pnr_data['To']})
   • Boarding: {pnr_data['BoardingStationName']}

⏰ Timing Information
   • Departure: {pnr_data['DepartureTime']}
   • Arrival: {pnr_data['ArrivalTime']}
   • Duration: {pnr_data['Duration']}
   • Platform: {pnr_data['ExpectedPlatformNo']}

💰 Fare Details
   • Ticket Fare: ₹{pnr_data['TicketFare']}

📅 Booking Date: {pnr_data['BookingDate']}
🎫 Quota: {pnr_data['Quota']}
{passenger_details}

Coach Position:\n
{formatted_coach_positions}
"""

    return message


def format_live_train_status(data):
    """
    Format the live train status data into a readable message
    
    Args:
        data (dict): Train status data from API
        
    Returns:
        str: Formatted status message
    """
    if not data or not isinstance(data, dict):
        return "❌ No data available for this train"
    
    try:
        # Basic train information
        train_info = (
            f"🚂 {data.get('train_name', 'N/A')} ({data.get('train_number', 'N/A')})\n"
            f"📅 Date: {data.get('train_start_date', 'N/A')}\n"
            f"🛤️ Route: {data.get('source_stn_name', 'N/A')} → {data.get('dest_stn_name', 'N/A')}\n"
        )

        # Current status information
        current_status = (
            f"📍 Current Location: {data.get('current_station_name', 'N/A')}\n"
            f"⏰ Last Updated: {data.get('status_as_of', 'N/A')}\n"
            f"⌛ Delay: {data.get('delay', '0')} minutes\n"
        )



        # Additional information if available
        additional_info = ""
        if data.get("platform_number"):
            additional_info += f"🚉 Platform: {data.get('platform_number')}\n"
        
        if data.get("distance_covered"):
            additional_info += f"📏 Distance Covered: {data.get('distance_covered')} km\n"

        # Combine all sections
        return (
            "🚂 Live Train Status\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{train_info}\n"
            f"{current_status}\n"
            f"{timing_info}\n"
            f"{additional_info}"
        ).strip()

    except Exception as e:
        return f"❌ Error formatting train status: {str(e)}"


def get_live_status(train_no, day):
    """
    Get live train status from the API
    
    Args:
        train_no (str): Train number
        day (str): Day selection (1-5)
        
    Returns:
        str: Formatted status message
    """
    url = f"https://irctc1.p.rapidapi.com/api/v1/liveTrainStatus?trainNo={train_no}&startDay={day}"
    
    try:
        response = requests.get(url, headers=train_headers)
        response.raise_for_status()
        data = response.json()
        
        if data.get("status"):
            return format_live_train_status(data.get("data", {}))
        else:
            return "❌ Unable to fetch train status. Please verify the train number and try again."
            
    except requests.exceptions.RequestException as e:
        return f"❌ Error fetching status details: {str(e)}"


# Usage in your code:
def get_pnr(pnr):
    url = f"https://irctc1.p.rapidapi.com/api/v3/getPNRStatus?pnrNumber={pnr}"
    
    try:
        response = requests.get(url, headers=train_headers)
        response.raise_for_status()
        data = response.json()
        print(data)
        formatted_message = format_pnr_details(data.get('data', {}))
        print(formatted_message)
        return formatted_message
    except requests.exceptions.RequestException as e:
        return f"❌ Error fetching PNR details: {e}"


@app.route("/whatsapp", methods=['POST'])
def whatsapp_reply():
    # from_number = request.form.get('From')
    # print(f"User's WhatsApp Number: {from_number}")
    
    incoming_msg = request.values.get('Body', '').strip().lower()
    sender = request.values.get('From', '').strip()
    response = MessagingResponse()
    message = response.message()
  
    session_data = get_session_data()  # Get or create session data

    # Restart command - Initialize a new session
    if incoming_msg == 'restart':
        session_data = handle_restart(sender, message)
        save_session_data(session_data)  # Save the new session data
        return str(response)

    # If it's the first message after the HTML is sent (e.g., "Hi"), show the options
    if session_data["step"] == 0:
        # This step happens after the initial greeting has been sent
        message.body("Hello! 👋 I’m here to assist you with bookings.\n\nPlease choose an option:\n🚉1. Book a Train.\n📝2.PNR Status.\n📍3.Live Train Status\n✈️4. Book a Flight.")
        session_data["step"] = 1  # Transition to the main menu step after receiving input
        save_session_data(session_data)  # Save session data after showing options
        return str(response)

    # Handle main menu selection after user input
    if session_data["step"] == 1:
        if incoming_msg == "1":
            message.body("🚉 You've selected Train Booking.\n\nPlease enter your source station (e.g., New Delhi or NDLS):")
            session_data["step"] = 11
        elif incoming_msg == "4":
            message.body("✈️ You've selected Flight Booking.\n\nPlease enter your departure airport (e.g., New Delhi or DEL ):")
            session_data["step"] = 10
        elif incoming_msg == "2":
            message.body("🔢 Please enter your PNR number:")
            session_data["step"] = 20  # New step for PNR check
        elif incoming_msg == "3":
            message.body("please enter train number and train start day option(1 to 5) \n1 = today \n2 = 1 Day Ago(yesterday) \n3 = 2 Day Ago \n4= 3 Day Ago \n5 = 4 Day Ago \nExample: 12345 1")
            session_data["step"] = 21
        else:
            message.body("❌ Invalid choice. Please reply with a number between 1 and 4.")
            session_data["step"] = 0  # Reset to main menu
        save_session_data(session_data)  # Save the updated session data
        return str(response)

    # Handle train booking steps (from step 2 to 9 or step 11)
    elif 2 <= session_data["step"] <= 9 or session_data["step"] == 11:
        session = handle_train_booking(session_data, incoming_msg, message)

    # Handle flight booking steps (from step 10 to 18)
    elif 10 <= session_data["step"] <= 19 and session_data["step"] != 11:
        session = handle_flight_booking(session_data, incoming_msg, message)

    elif session_data["step"] == 20:
        pnr = incoming_msg
        session_data["data"]["pnr"] = pnr
        formatted_response = get_pnr(session_data["data"]["pnr"])
        message.body(formatted_response)
        session_data["step"] = 0  # Return to main menu after PNR check
        save_session_data(session_data)
        return str(response)
    elif session_data["step"] == 21:
        try:
            # Split the incoming message into train number and day
            message_parts = incoming_msg.strip().split()
            
            if len(message_parts) != 2:
                message.body(
                    "❌ Invalid format. Please provide both train number and day.\n"
                    "Format: <train_number> <day>\n"
                    "Example: 12345 1"
                )
                return str(response)
                
            train_no, user_day = message_parts
            
            # Validate day input
            if not user_day.isdigit() or int(user_day) not in range(1, 6):
                message.body("❌ Invalid day selection. Please choose a number between 1-5.")
                return str(response)
            
            # Map user input day to API day value
            day_mapping = {
                "1": "0",  # Today
                "2": "1",  # Yesterday
                "3": "2",  # 2 days ago
                "4": "3",  # 3 days ago
                "5": "4",   # 4 days ago
            }
            
            api_day = day_mapping.get(user_day)

            
            # Get live status with mapped day value
            status_response = get_live_status(train_no, api_day)
            message.body(status_response)
            
            # Reset session
            session_data["step"] = 0
            save_session_data(session_data)
            
        except Exception as e:
            message.body(f"❌ An error occurred: {str(e)}")
            session_data["step"] = 0
            save_session_data(session_data)
        
        return str(response)
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




# Route to display the details in an HTML table (user-friendly)
@app.route('/train_bookings', methods=['GET'])
def show_bookings_html():
    bookings = fetch_bookings()
    # Pass the formatted bookings to the HTML template
    return render_template("bookings.html", bookings=bookings)



if __name__ == "__main__":
    create_database()
    app.run(debug=True)
