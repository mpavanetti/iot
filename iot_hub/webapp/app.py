from flask import Flask, render_template, redirect
import platform
import socket

from application.Hardware import Hardware


app = Flask(__name__)

@app.route('/')
def routing():
    return redirect('/home')

@app.route('/home')
def home():
    return render_template('home.html',menu="home")

@app.route('/hardware')
def hardware():
    hardware= Hardware()
    return render_template('hardware.html',
                           menu="hardware",
                           network=hardware.get_all_network(),
                           hardware= hardware.get_all_hardware(),
                          python=hardware.get_all_python())

if __name__ == '__main__':
	app.run(debug=True, port=8001, host='0.0.0.0') 
