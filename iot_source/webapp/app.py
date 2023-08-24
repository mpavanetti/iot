from flask import Flask, render_template, redirect
import platform
import socket

import urllib.request

external_ip = urllib.request.urlopen('https://ident.me').read().decode('utf8')


app = Flask(__name__)

@app.route('/')
def routing():
    return redirect('/home')

@app.route('/home')
def home():
    return render_template('home.html',menu="home")

@app.route('/hardware')
def hardware():
    return render_template('hardware.html',
                           menu="hardware",
                           uname=platform.uname(),
                           fqdn=socket.getfqdn(),
                           external_ip=external_ip,
                           local_ip=socket.gethostbyname(socket.getfqdn()))