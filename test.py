from flask import (
    Flask,
    url_for,
    request,
    render_template,
    send_from_directory,
    jsonify,
    send_file,
    session,
    redirect,
)
from model import Booking, Flight
from waitress import serve
from helper import (
    parse_description,
    parse_departure,
    parse_arrival,
    check_flight_pattern,
    extract_flight_info,
    abbreviated_place_name,
    abbreviate_airport_name,
    md5_hash,
)
import re
import os
from weasyprint import HTML
from datetime import datetime
import threading
from flask import Response
import hashlib
from db import db, Register
from datetime import timedelta
import pytz
from pdf_manager import PDFManager
import zipfile
import tempfile
from flask import after_this_request
from collections import defaultdict
import base64

app = Flask(__name__)
app.secret_key = os.urandom(24)
db_path = "dblfihgt.db"
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.abspath(db_path)}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'static\\uploads')
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
db.init_app(app)

# global_booking = None
logo_path = "vietnam-airline-logo.png"

user_requests = defaultdict(int) 
MAX_REQUESTS_PER_DAY = 5 
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

if not os.path.exists(db_path):
    print("Database not found. Creating new database.")
    with app.app_context():
        db.create_all()
else:
    print("Database already exists.")

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/parsepage", methods=["GET"])
def parse_page():
    if "user_id" in session and session["usertype"] in ["admin", "guest"]:
        user_id = session.get("user_id")  
        user = Register.query.filter_by(id=session["user_id"]).first()
        booking = Booking(None, None, [], None, [], Flight(), Flight())
        if user:
            return render_template("parse_data.html", user_name=user.user, booking=booking, user_id=user_id)
        else:
            return "Không tìm thấy thông tin người dùng"
    else:
        return redirect(url_for("login"))
@app.route("/parsedata", methods=["POST"])
def parse_data():
    ip_address = request.remote_addr
    is_logged_in = "user_id" in session and session["usertype"] in ["admin", "guest"]
    if not is_logged_in:
        today = datetime.now().date()
        if user_requests[ip_address] >= MAX_REQUESTS_PER_DAY:
            return redirect(url_for("login_page"))
        user_requests[ip_address] += 1
    if request.method == "POST":
        global logo_path
        try:
            logo= request.form.get("logo_ticket")
            print(logo)
            if logo == "vna":
                logo_path = "vietnam-airline-logo.png"
            elif logo == "vietjet":
                logo_path = "vietjet-logo.png"
            elif logo == "bamboo":
                logo_path = "bamboo-logo.png"

            image = request.files.get("image")
            if image:
                if allowed_file(image.filename):
                    filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{image.filename}"
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    image.save(filepath)
                    session['uploaded_image_path'] = filepath
                    print("Session data on download_pdf:", session['uploaded_image_path'])
                else:
                    return jsonify({"error": "Ảnh tải lên không hợp lệ, chỉ chấp nhận các định dạng như .jpg, .png, .jpeg"}), 400
            else:
                session['uploaded_image_path'] = None
                filename = None


            form_data = request.form.get("data", "").replace("\r\n", "\n")
            data = f'"""{form_data}"""'

            booking_ref = re.search(r"BOOKING REF:\s*(\w+)", data)
            date_issue = re.search(r"DATE:\s*(\d{1,2}\s+\w+\s+\d{4})", data)

            reservation_status = "CONFIRMED"

            ticket_matches = re.findall(r"TICKET:\s*(.*?)\s*FOR\s*(.*?)\n", data)
            tickets = (
                [ticket[0].strip() for ticket in ticket_matches] if ticket_matches else []
            )
            passenger_names = (
                [ticket[1].strip() for ticket in ticket_matches] if ticket_matches else []
            )

            flight_data_pattern = re.compile(
                r"(FLIGHT\s+.+?)\n-+\n(.+?)(EQUIPMENT:\s*.+?)\n", re.DOTALL
            )
            flight_data_matches = re.findall(flight_data_pattern, data)

            data1 = flight_data_matches[0] if len(flight_data_matches) > 0 else None
            data2 = flight_data_matches[1] if len(flight_data_matches) > 1 else None

            def create_flight_object(flight_info):
                if not flight_info:
                    return None

                description = flight_info.get("Description", "").strip()
                departure = flight_info.get("Departure", "").strip()
                arrival = flight_info.get("Arrival", "").strip()
                economy_class = flight_info.get("Economy Class", "").strip()
                duration = flight_info.get("Duration", "").strip()
                baggage = (
                    flight_info.get("Baggage", "").strip()
                    if "Baggage" in flight_info
                    else None
                )
                meal = (
                    flight_info.get("Meal", "").strip() if "Meal" in flight_info else None
                )
                nonstop = (
                    flight_info.get("NonStop", "").strip()
                    if "NonStop" in flight_info
                    else None
                )
                equipment = (
                    flight_info.get("Equipment", "").strip()
                    if "Equipment" in flight_info
                    else None
                )

                des, des_time = parse_description(description)
                departure_place, departure_terminal, departure_time = parse_departure(
                    departure
                )
                arrival_place, arrival_terminal, arrival_time = parse_arrival(arrival)

                return Flight(
                    des,
                    departure,
                    arrival,
                    duration,
                    equipment,
                    baggage,
                    meal,
                    nonstop,
                    departure_place=departure_place,
                    departure_terminal=departure_terminal,
                    departure_time=departure_time,
                    arrival_place=arrival_place,
                    arrival_terminal=arrival_terminal,
                    arrival_time=arrival_time,
                    des_time=des_time,
                    economy_class=economy_class,
                )

            if data1 or data2:
                missing_info = None
                if data1 and data2:
                    missing_info = check_flight_pattern(data1, data2)
                    flight_info1 = extract_flight_info(data1)
                    flight_info2 = extract_flight_info(data2)
                    flight1 = create_flight_object(flight_info1)
                    flight2 = create_flight_object(flight_info2)
                elif data1 and not data2:
                    flight_info1 = extract_flight_info(data1)
                    flight1 = create_flight_object(flight_info1)
                    flight2 = None
                else:
                    flight1 = flight2 = None

                global_booking = Booking(
                    booking_ref.group(1) if booking_ref else None,
                    date_issue.group(1) if date_issue else None,
                    passenger_names,
                    reservation_status,
                    tickets,
                    flight1,
                    flight2,
                )
                session['booking'] = global_booking.to_dict()
                return jsonify({
                    "booking": global_booking.to_dict(),
                    "missing_info": missing_info,
                    "logo_path": logo_path,
                    "logo_user": filename
                }), 200

            return jsonify({"error": "Không có dữ liệu chuyến bay"}), 400

        except Exception as e:
            print(f"Lỗi: {e}")
            return jsonify({"error": "Lỗi xử lý dữ liệu"}), 500
@app.route("/download_pdf", methods=["POST"])
def download_pdf():
    global logo_path

    booking_data = session.get('booking')
    data = request.get_json()
    passenger_name = data.get("passenger_name")
    selected_fields = data.get("selected_fields", [])

    global_booking = Booking.from_dict(booking_data)
    print("Selected fields:", selected_fields)

    if not passenger_name:
        return "Passenger name is required", 400

    try:
        passenger_index = global_booking.passenger_name.index(passenger_name)
    except ValueError:
        return jsonify({"error": "Không tìm thấy hành khách trong danh sách"}), 404

    filtered_booking = Booking(
        booking_ref=global_booking.booking_ref,
        date_issue=global_booking.date_issue,
        passenger_name=[global_booking.passenger_name[passenger_index]],
        reservation_status=global_booking.reservation_status,
        ticket=[global_booking.ticket[passenger_index]],
        flight1=global_booking.flight1,
        flight2=global_booking.flight2,
    )
    print("Session data on download_pdf:", session)
    logo_user_path = session.get('uploaded_image_path')
    print("Logo user path:",logo_user_path)
    logo_user_base64 = None

    if logo_user_path and os.path.exists(logo_user_path):
        with open(logo_user_path, "rb") as logo_file:
            logo_user_base64 = base64.b64encode(logo_file.read()).decode('utf-8')
    # print(logo_user_base64)
    pdf_path = PDFManager.print_pdf(filtered_booking, passenger_name, logo_path, selected_fields, logo_user_base64)
    with open(pdf_path, "rb") as f:
        pdf_data = f.read()
    response = Response(pdf_data, mimetype="application/pdf")
    response.headers.set(
        "Content-Disposition",
        f"attachment; filename={PDFManager.create_pdf_filename(passenger_name, filtered_booking)}",
    )

    try:
        os.remove(pdf_path)
    except Exception as e:
        print(f"Error removing file: {e}")

    return response
@app.route("/download_all_pdf", methods=["POST"])
def download_all_pdf():
    global logo_path
    booking_data = session.get('booking')
    global_booking = Booking.from_dict(booking_data)
    data = request.get_json()
    selected_fields = data.get("selected_fields", [])
    logo_user = session.get('uploaded_image_base64')
    zip_path = PDFManager.create_all_pdfs(global_booking, logo_path, selected_fields, logo_user)
    print(zip_path)

    return send_file(
        zip_path,
        mimetype="application/zip",
        as_attachment=True,
        download_name='all_tickets.zip',
    )
@app.route("/")
def home():
    user_id = session.get("user_id")  
    usertype = session.get("usertype")  
    booking = Booking(None, None, [], None, [], Flight(), Flight())
    return render_template("parse_data.html", booking=booking, user_id=user_id, usertype=usertype)
@app.route("/login_page")
def login_page():
    return render_template("login.html")
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        print(request.form["password"])
        email = request.form["email"]
        password = md5_hash(request.form["password"])
        user = Register.query.filter_by(email=email, password=password).first()
        print(email)

        if user:
            session["user_id"] = user.id
            session["usertype"] = user.usertype
            session.permanent = True
            if user.usertype == "guest":
                session.permanent = True 
                app.permanent_session_lifetime = timedelta(hours=1) 
            elif user.usertype == "admin":
                session.permanent = True
                app.permanent_session_lifetime = timedelta(days=1)
            if user.usertype == "admin":
                return redirect(url_for("admin_overview"))
            elif user.usertype in ["guest"]:
                return redirect(url_for("parse_page"))
        else:
            return "Notok", 400
    return render_template("login.html")
@app.route("/account", methods=["GET"])
def admin_account():
    if "user_id" in session and session["usertype"] in ["admin", "guest"]:
        user = Register.query.filter_by(id=session["user_id"]).first()
        if user:
            return render_template("admin/account.html", user_name=user.user, user_type=user.usertype, email=user.email)
        else:
            return "Không tìm thấy thông tin người dùng"
    else:
        return redirect(url_for("login"))
@app.route("/logout", methods=["GET"])
def logout():
    session.clear()
    return "OK", 200
@app.before_request
def limit_guest_session():
    if "user_id" in session and session["usertype"] == "guest":
        timezone = pytz.timezone('Asia/Ho_Chi_Minh')
        now = datetime.now(timezone)

        if 'last_activity' not in session:
            session['last_activity'] = now
        else:
            last_activity = session['last_activity'].astimezone(timezone)
            if (now - last_activity) > timedelta(hours=1):
                session.pop('user_id', None)
                session.pop('usertype', None)
                return redirect(url_for("login_page"))

        session['last_activity'] = now
@app.route("/numbertool", methods=["GET"])
def number_tool():
    if "user_id" in session and session["usertype"] in ["admin", "guest"]:
        user_id = session.get("user_id")  
        user = Register.query.filter_by(id=session["user_id"]).first()
        if user:
            return render_template("number.html", user_name=user.user, user_id=user_id)
        else:
            return "Không tìm thấy thông tin người dùng"
    else:
        return redirect(url_for("login"))    
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
    # serve(app, host='0.0.0.0', port=5000)
