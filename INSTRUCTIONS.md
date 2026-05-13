# Library Management System (LibMS)

This is a premium, GSoC-level Library Management System built with Flask and Python. It features a modern, responsive, and glassmorphic user interface.

## Prerequisites

Before running the application, ensure you have the following installed on your system:
- **Python 3.8+** (https://www.python.org/downloads/)
- **pip** (Python package installer, usually comes with Python)

## Setup and Installation

Follow these steps to run the project locally:

### 1. Extract the Project
Extract this ZIP file to a folder of your choice and open a terminal (or command prompt) in that folder.

### 2. Create a Virtual Environment (Recommended)
It's best practice to use a virtual environment to manage dependencies.
```bash
# On Windows:
python -m venv venv
venv\Scripts\activate

# On macOS/Linux:
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies
Install all the required Python packages from the `requirements.txt` file:
```bash
pip install -r requirements.txt
```

### 4. Run the Application
Start the Flask development server:
```bash
python app.py
```
*(Alternatively, you can run `flask run` if you have Flask CLI setup)*

### 5. Access the Application
Once the server is running, you will see an output like:
`* Running on http://127.0.0.1:5000`

Open your web browser and go to: **http://127.0.0.1:5000**

## Project Features
- **Premium UI/UX**: Dark/Light mode, Glassmorphism, and modern typography (Outfit/Inter).
- **Dashboard**: Real-time stats, dynamic digital library card, and activity overview.
- **Catalogue**: Browse books, search dynamically, and check availability.
- **Admin Panel**: Manage books, users, and fines efficiently.
- **Notifications & Fines**: Automated notifications for holds and overdue books.
