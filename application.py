import os

from flask import Flask, session, render_template, request, redirect, url_for, escape, jsonify, flash
from flask_session import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

# Error handlers
from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.routing import BuildError

# for API request
import requests
from datetime import datetime

app = Flask(__name__)

# Check for environment variable
if not os.getenv("HEROKU_POSTGRESQL_CHARCOAL_URL"):
    raise RuntimeError("HEROKU_POSTGRESQL_CHARCOAL_URL is not set")

if not os.getenv("API_SECRET_KEY"):
    raise RuntimeError("API_SECRET_KEY is not set")

# Configure session to use filesystem
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
app.config["SECRET_KEY"] = os.urandom(24)
Session(app)

api_key = os.getenv("API_SECRET_KEY")

# Set up database
engine = create_engine(os.getenv("HEROKU_POSTGRESQL_CHARCOAL_URL"), connect_args={'sslmode': 'require'})
db = scoped_session(sessionmaker(bind=engine))

# pylint: disable=no-member

@app.route("/", methods=["GET", "POST"])
def index():
    # if a user is logged-in, show search box
    if "user_id" in session:
        return render_template("search.html")
    else:
        return render_template("index.html")

# Sign up
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        # Get a name and a password from a user
        name = request.form.get("name")
        password = request.form.get("password")
        confirmation = request.form.get("confirmation")

        # (additional) server-side confirmation
        if password != confirmation:
            flash("Passwords don't match.")
            return redirect(request.referrer)

        # If the user name already exists in the database, return an error message.
        try:
            db.execute("INSERT INTO users (name, password) VALUES (:name, crypt(:password, gen_salt('md5')))",
                {"name": name, "password": password})
            db.commit()
        except IntegrityError:
            db.rollback()
            flash("The username already exists. Pick another one.")
            return redirect(request.referrer)

        return render_template("welcome.html")
        
    else:
        return render_template("signup.html")

# Sign in
@app.route("/signin", methods=["GET", "POST"])
def signin():
    if request.method == "POST":
        # Get a name and a password from a user
        name = request.form.get("name")
        password = request.form.get("password")

        # query for signing in
        try:
            user = db.execute("SELECT * FROM users WHERE name = :name AND password = CRYPT(:password, password)",
                {"name": name, "password": password}).fetchone()
        except OperationalError:
            db.rollback()
            return redirect(url_for("server_error_handler"))

        # if the matching user does not exist in the database, send an error message
        if user is None:
            flash("The username or password is wrong. Please sign in again.")
            return redirect(request.referrer)
        else:
            session["user_id"] = db.execute("SELECT id, name FROM users WHERE name = :name AND password = CRYPT(:password, password)",
                {"name": name, "password": password}).fetchone()
            return redirect(url_for("index"))
    else:
        return render_template("signin.html")

# Sign out
@app.route("/signout")
def signout():
    # Remove the user_id from the session if it's there
    if "user_id" in session:
        session.pop("user_id", None)
        return redirect(url_for("index"))
    else:
        return redirect(url_for("index"))

# Search location
@app.route("/search", methods=["POST"])
def search():
    # Capitalize all characters of input value and get a list of locations
    location = '%' + request.form.get("location").upper() + '%'
    try:
        results = db.execute("SELECT * FROM locations WHERE zipcode::varchar LIKE :location OR city LIKE :location",
            {"location": location}).fetchall()
    except OperationalError:
        db.rollback()
        return redirect(url_for("server_error_handler"))

    # if no matching location in the database
    if not results:
        return render_template("search.html", message="No locations in the database")

    # if location data exists
    return render_template("search.html", results=results)

# location info
@app.route("/location/<int:location_id>", methods=["GET", "POST"])
def location(location_id):
    if "user_id" in session:
        # if the user submit a comment, commit INSERT query into the database
        if request.method == "POST":
            name = session["user_id"][1]
            comment = request.form.get("comment")
            try:
                db.execute("INSERT INTO checkins (name, comment, time, location_id) VALUES (:name, :comment, CURRENT_TIMESTAMP(0), :location_id)",
                    {"name": name, "comment": comment, "location_id": location_id})
                db.commit()
            except OperationalError:
                db.rollback()
                return redirect(url_for("server_error_handler"))

            return redirect(request.referrer)
        
        # Get geographical data
        try:
            location = db.execute("SELECT * FROM locations WHERE id = :id",
                {"id": location_id}).fetchone()
        except OperationalError:
            db.rollback()
            return redirect(url_for("server_error_handler"))

        if location is None:
            return render_template("search.html", message="No locations in the database")

        # Get weather data from https://darksky.net/dev
        # other HTTP query parameters options for units: auto, us, si
        res = requests.get(f"https://api.darksky.net/forecast/{api_key}/{location.lat},{location.long}?units=si&exclude=minutely,hourly,daily,alerts,flags")

        if res.status_code != 200:
            raise Exception("ERROR: API request unsuccessful.")

        data = res.json()
        
        # Convert UNIX timestamp from API request to string timestamp
        time = datetime.fromtimestamp(data["currently"]["time"])

        summary = data["currently"]["summary"]
        temperature = data["currently"]["temperature"]
        humidity = (data["currently"]["humidity"])*100
        pressure = data["currently"]["pressure"]
        windspeed = data["currently"]["windSpeed"]

        # Get comments data
        try:
            comments = db.execute("SELECT * FROM checkins WHERE location_id = :id",
                {"id": location_id}).fetchall()
            count = len(comments)
        except OperationalError:
            db.rollback()
            return redirect(url_for("server_error_handler"))

        return render_template("location.html", location=location, comments=comments, count=count,
                                time = time, summary=summary, temperature=temperature, humidity=humidity, pressure=pressure, windspeed=windspeed)
    
    # if the user is not logged-in
    return redirect(url_for('page_not_found'))

# user information
@app.route("/user/<string:name>")
def user(name):
    if "user_id" in session and session["user_id"][1] == name:
        return render_template("user.html")
    else:
        return redirect(url_for('page_not_found'))

# verifcation route
@app.route("/user/<string:name>/verification", methods=["GET", "POST"])
def verification(name):
    # routed from 'Change password' link
    if request.method == "GET" and session["user_id"][1] == name:
        return render_template("verification.html")

    # user verification
    elif request.method == "POST":
        name = session["user_id"][1]
        password = request.form.get("password")

        try:
            user = db.execute("SELECT * FROM users WHERE name = :name AND password = CRYPT(:password, password)",
                {"name": name, "password": password}).fetchone()
        except OperationalError:
            db.rollback()
            return redirect(url_for("server_error_handler"))

        # if the password is wrong
        if user is None:
            flash("The password is wrong. Please try again.")
            return redirect(request.referrer)
        # if the password is correct
        else:
            return render_template("newpassword.html")
            # status code 307 for POST request
            # return redirect(url_for("updatepassword"), code=307)

    else:
        return redirect(url_for('page_not_found'))

# Display new password setting
@app.route("/updatepassword", methods=["POST"])
def updatepassword():
    if request.method == "POST":
        user_id = session["user_id"][0]

        password = request.form.get("password")
        confirmation = request.form.get("confirmation")

        # (additional) server-side confirmation
        if password != confirmation:
            flash("Passwords don't match.")
            return redirect(request.referrer)
        
        try:
            db.execute("UPDATE users SET password = crypt(:password, gen_salt('md5')) WHERE id = :id",
                {"password": password, "id": user_id})
            db.commit()
            session.pop("user_id", None)
            return render_template("signin_again.html")
        except IntegrityError:
            db.rollback()
            flash("Your password is too short. Make longer password.")
            return redirect(request.referrer)

    # iff the request method is GET
    else:
        return redirect(url_for('page_not_found'))

# user's comment list
@app.route("/user/<string:name>/comment")
def comment(name):
    if "user_id" in session and session["user_id"][1] == name:
        # Get a list of comments
        try:
            comments = db.execute("SELECT * FROM checkins WHERE name=:name",
                {"name": name}).fetchall()
            count = len(comments)
        except OperationalError:
            db.rollback()
            return redirect(url_for("server_error_handler"))

        if not comments:
            return render_template("comments.html")
    
        return render_template("comments.html", comments=comments, count=count)

    # if the user is not logged-in
    else:
        return redirect(url_for('page_not_found'))

# Delete comments from My comment list
@app.route("/delete", methods=["POST"])
def delete():
    if request.method == "POST":
        comment_id = request.form.get("comment_id")
        try:
            db.execute("DELETE FROM checkins WHERE id=:id", {"id": comment_id})
            db.commit()
        except OperationalError:
            db.rollback()
            return redirect(url_for("server_error_handler"))

        return redirect(request.referrer)

# API overview page
@app.route("/api")
def api():
    return render_template("api.html")

# API access
@app.route("/api/location/<int:zipcode>")
def location_api(zipcode):
    try:
        location = db.execute("SELECT * FROM locations WHERE zipcode=:zipcode",
            {"zipcode": zipcode}).fetchone()
    except OperationalError:
        db.rollback()
        return redirect(url_for("server_error_handler"))

    if location is None:
        return jsonify({"ERROR": "Invalid zipcode"}), 422

    return jsonify({
        "Zipcode": location.zipcode,
        "City": location.city,
        "Latitude": location.lat,
        "Longitude": location.long,
        "Population": location.population
    })

# If any user tries to access to nonexistent routes, render an error page
@app.errorhandler(404)
def page_not_found(error):
    return render_template("error.html", message="The requested URL was not found on this server."), 404

@app.errorhandler(BuildError)
def build_error(error):
    return render_template("error.html", message="The requested URL was not found on this server."), 404

# internal server error
@app.errorhandler(500)
def server_error_handler(error):
    return render_template("error.html", message="Database connection failed. Please try again."), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0')